"""Application-scoped tool publications and external execution."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.infrastructure.database import Database
from zhiheng.modules.mcp.contracts import ExternalToolResult, PublicationCreate, PublicationView, ToolDefinition
from zhiheng.modules.mcp.orm import McpCall, McpPublication, McpServer
from zhiheng.modules.mcp.transport import McpTransport
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

MAX_APPLICATION_TOOLS = 100


class McpPublications:
    def __init__(self, database: Database, cipher: CredentialCipher, transport: McpTransport) -> None:
        self.database, self.cipher, self.transport = database, cipher, transport

    async def _publications(
        self, session: AsyncSession, application_id: UUID, ids: Sequence[UUID] | None = None
    ) -> list[tuple[McpPublication, McpServer]]:
        query = (
            select(McpPublication, McpServer)
            .join(McpServer, McpServer.id == McpPublication.server_id)
            .where(McpPublication.application_id == application_id, McpPublication.withdrawn_at.is_(None))
            .order_by(McpPublication.name)
        )
        if ids is not None:
            query = query.where(McpPublication.id.in_(ids))
        return [(publication, server) for publication, server in await session.execute(query)]

    def _available(self, publication: McpPublication, server: McpServer) -> bool:
        return (
            server.enabled
            and server.synced_at == publication.source_synced_at
            and server.endpoint in self.transport.allowed_endpoints
            and ToolDefinition.model_validate(publication.definition).can_publish
        )

    def _view(self, publication: McpPublication, server: McpServer) -> PublicationView:
        return PublicationView(
            id=publication.id,
            server_name=server.name,
            tool_name=ToolDefinition.model_validate(publication.definition).name,
            name=publication.name,
            available=self._available(publication, server),
        )

    async def for_application(self, session: AsyncSession, application_id: UUID) -> list[PublicationView]:
        return [self._view(row, server) for row, server in await self._publications(session, application_id)]

    async def candidate_ids(self, session: AsyncSession, application_id: UUID) -> set[UUID]:
        return {row.id for row in await self.for_application(session, application_id) if row.available}

    async def publish(self, session: AsyncSession, application_id: UUID, body: PublicationCreate) -> PublicationView:
        server = await session.get(McpServer, body.server_id, with_for_update=True)
        if server is None or not server.enabled or server.synced_at is None or server.version != body.expected_version:
            raise AppError('mcp_publication_unavailable', '服务已停用或配置已变化，请刷新后重试', 409)
        self.transport.allowed(server.endpoint)
        definition = next(
            (ToolDefinition.model_validate(tool) for tool in server.tools if tool['name'] == body.tool_name), None
        )
        if definition is None:
            raise AppError('mcp_tool_missing', '工具不存在，请重新同步', 404)
        if definition.read_only_hint is False:
            raise AppError('mcp_publication_not_read_only', '上游声明此工具可能修改数据，无法发布', 422)
        if not definition.can_publish:
            raise AppError('mcp_publication_sync_required', '请重新同步工具以确认上游只读声明', 409)
        if len(await self._publications(session, application_id)) >= MAX_APPLICATION_TOOLS:
            raise AppError('mcp_publication_limit', '应用最多发布 100 个 MCP 工具', 409)
        token = self.cipher.decrypt(server.encrypted_token) if server.encrypted_token else None
        if token and json.dumps(token)[1:-1] in json.dumps({'alias': body.name, 'tool': definition.model_dump()}):
            raise AppError('mcp_publication_invalid', '工具定义包含服务凭据', 422)
        row = McpPublication(
            id=uuid4(),
            application_id=application_id,
            server_id=server.id,
            source_synced_at=server.synced_at,
            name=body.name,
            definition=definition.model_dump(mode='json'),
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except IntegrityError:
            raise AppError('mcp_publication_conflict', '该发布名称已被此应用使用', 409) from None
        return self._view(row, server)

    async def withdraw(self, session: AsyncSession, application_id: UUID, publication_id: UUID) -> None:
        row = await session.get(McpPublication, publication_id, with_for_update=True, populate_existing=True)
        if row is None or row.application_id != application_id or row.withdrawn_at is not None:
            raise AppError('mcp_publication_missing', '发布记录不存在', 404)
        row.withdrawn_at = utcnow()

    async def tools(self, session: AsyncSession, application_id: UUID, ids: Sequence[UUID]) -> list[dict[str, object]]:
        items = []
        for row, server in await self._publications(session, application_id, ids):
            if self._available(row, server):
                definition = ToolDefinition.model_validate(row.definition)
                item: dict[str, object] = {
                    'name': row.name,
                    'description': definition.description,
                    'inputSchema': definition.input_schema,
                }
                if definition.read_only:
                    item['annotations'] = {'readOnlyHint': True}
                items.append(item)
        return items

    async def execute(
        self,
        application_id: UUID,
        key_id: UUID,
        caller_id: UUID,
        name: str,
        arguments: dict[str, object],
        authorize: Callable[[AsyncSession], Awaitable[Sequence[UUID]]],
    ) -> ExternalToolResult:
        async with self.database.sessions() as session:
            allowed = await authorize(session)
            row, server = await self._resolve(session, application_id, allowed, name)
            endpoint = server.endpoint
            token = self.cipher.decrypt(server.encrypted_token) if server.encrypted_token else None
            definition = ToolDefinition.model_validate(row.definition)
        began = monotonic()
        status, code = 'failed', 'mcp_interrupted'
        try:
            result = await self.transport.invoke(endpoint, token, definition, arguments)
            async with self.database.sessions() as session:
                allowed = await authorize(session)
                current, _ = await self._resolve(session, application_id, allowed, name)
                if current.id != row.id:
                    raise self._missing()
            status, code = ('failed', 'mcp_tool_failed') if result.is_error else ('succeeded', None)
            return result
        except AppError as error:
            code = error.code
            raise
        except asyncio.CancelledError:
            status, code = 'cancelled', 'mcp_cancelled'
            raise
        finally:
            async with self.database.sessions.begin() as session:
                session.add(
                    McpCall(
                        id=uuid4(),
                        server_id=row.server_id,
                        caller_id=caller_id,
                        application_id=application_id,
                        key_id=key_id,
                        tool_name=row.name,
                        status=status,
                        error_code=code,
                        duration_ms=round((monotonic() - began) * 1000),
                        created_at=utcnow(),
                    )
                )

    async def _resolve(
        self, session: AsyncSession, application_id: UUID, ids: Sequence[UUID], name: str
    ) -> tuple[McpPublication, McpServer]:
        for row, server in await self._publications(session, application_id, ids):
            if row.name == name and self._available(row, server):
                return row, server
        raise self._missing()

    @staticmethod
    def _missing() -> AppError:
        return AppError('mcp_tool_unavailable', '工具不可用或未授权', 404)
