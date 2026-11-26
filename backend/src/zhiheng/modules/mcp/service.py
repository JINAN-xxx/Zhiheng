"""Manage MCP connections, tool grants and workspace calls."""

import asyncio
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.infrastructure.database import Database
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.mcp.contracts import (
    BoundTool,
    CallView,
    CatalogTool,
    ManagedTool,
    ServerCreate,
    ServerUpdate,
    ServerView,
    ToolDefinition,
    ToolGrant,
    ToolSelection,
)
from zhiheng.modules.mcp.orm import McpCall, McpServer
from zhiheng.modules.mcp.publications import McpPublications
from zhiheng.modules.mcp.transport import McpTransport
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import has_capability, require_capability
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

MAX_SERVERS_PER_LIST = 100
MAX_CALLS_PER_LIST = 50


class McpService:
    def __init__(
        self,
        database: Database,
        identity: IdentityService,
        cipher: CredentialCipher,
        allowed_endpoints: tuple[str, ...],
    ) -> None:
        self.database = database
        self.identity = identity
        self.cipher = cipher
        self.transport = McpTransport(allowed_endpoints)
        self.publications = McpPublications(database, cipher, self.transport)

    async def servers(self, session: AsyncSession, principal: Principal) -> list[ServerView]:
        rows = await session.scalars(
            select(McpServer).order_by(McpServer.name, McpServer.id).limit(MAX_SERVERS_PER_LIST)
        )
        return [self._view(row, principal) for row in rows]

    async def catalog(self, session: AsyncSession, principal: Principal) -> list[CatalogTool]:
        if not has_capability(principal, 'mcp', 'use'):
            return []
        roles = await self.identity.active_role_ids(session, principal.id)
        rows = await session.scalars(
            select(McpServer)
            .where(McpServer.enabled.is_(True))
            .order_by(McpServer.name, McpServer.id)
            .limit(MAX_SERVERS_PER_LIST)
        )
        return [
            CatalogTool.model_validate(
                {**tool.model_dump(), 'server_id': row.id, 'server_name': row.name, 'version': row.version}
            )
            for row in rows
            if row.endpoint in self.transport.allowed_endpoints
            for tool in self._tools(row)
            if self._granted(principal, tool, roles)
        ]

    async def create(self, session: AsyncSession, principal: Principal, body: ServerCreate) -> ServerView:
        row = McpServer(
            id=uuid4(),
            name=body.name,
            endpoint=self.transport.allowed(body.endpoint),
            encrypted_token=self._encrypt(body),
            enabled=False,
            version=1,
            tools=[],
        )
        session.add(row)
        await session.flush()
        return self._view(row, principal)

    async def update(
        self, session: AsyncSession, principal: Principal, server_id: UUID, body: ServerUpdate
    ) -> ServerView:
        row = await self._server(session, server_id, lock=True)
        self._version(row, body.expected_version)
        endpoint = (
            self.transport.allowed(body.endpoint) if body.endpoint != row.endpoint or body.enabled else row.endpoint
        )
        if body.clear_token and body.token is not None:
            raise AppError('mcp_token_invalid', '请在替换和清除凭据中选择一项', 422)
        if endpoint != row.endpoint or body.clear_token or body.token is not None:
            row.tools = []
            row.synced_at = None
            row.encrypted_token = self._encrypt(body)
        row.name, row.endpoint = body.name, endpoint
        if body.enabled and row.synced_at is None:
            raise AppError('mcp_sync_required', '请先同步工具再启用服务', 409)
        row.enabled = body.enabled
        row.version += 1
        return self._view(row, principal)

    async def sync(self, principal: Principal, server_id: UUID, version: int) -> ServerView:
        async with self.database.sessions() as session:
            current = await self.identity.principal_for_user(session, principal.id)
            require_capability(current, 'mcp', 'configure')
            row = await self._server(session, server_id)
            self._version(row, version)
            endpoint, token = row.endpoint, self._token(row)
        tools = await self.transport.discover(endpoint, token)
        async with self.database.sessions.begin() as session:
            current = await self.identity.principal_for_user(session, principal.id)
            require_capability(current, 'mcp', 'configure')
            row = await self._server(session, server_id, lock=True)
            self._version(row, version)
            previous = {tool.name: tool for tool in self._tools(row)}
            synced: list[ManagedTool] = []
            for tool in tools:
                old = previous.get(tool.name)
                unchanged = old is not None and old.model_dump(exclude={'enabled', 'role_ids'}) == tool.model_dump()
                synced.append(old if unchanged and old is not None else ManagedTool.model_validate(tool.model_dump()))
            row.tools = [tool.model_dump(mode='json') for tool in synced]
            row.synced_at = utcnow()
            row.version += 1
            return self._view(row, current)

    async def grant(
        self, session: AsyncSession, principal: Principal, server_id: UUID, name: str, body: ToolGrant
    ) -> ServerView:
        row = await self._server(session, server_id, lock=True)
        self._version(row, body.expected_version)
        tools = self._tools(row)
        tool = next((item for item in tools if item.name == name), None)
        if tool is None:
            raise AppError('mcp_tool_missing', '工具不存在，请重新同步', 404)
        if body.enabled and not tool.read_only:
            raise AppError('mcp_read_only_required', '只能启用声明为只读的工具', 422)
        role_ids = set(body.role_ids)
        roles = await self.identity.active_role_options(session, ids=role_ids)
        if role_ids != set(roles):
            raise AppError('mcp_role_invalid', '所选角色不可用', 422)
        tool.enabled = body.enabled
        tool.role_ids = sorted(role_ids)
        row.tools = [item.model_dump(mode='json') for item in tools]
        row.version += 1
        return self._view(row, principal)

    async def calls(self, session: AsyncSession, server_id: UUID) -> list[CallView]:
        await self._server(session, server_id)
        rows = await session.scalars(
            select(McpCall)
            .where(McpCall.server_id == server_id)
            .order_by(McpCall.created_at.desc(), McpCall.id)
            .limit(MAX_CALLS_PER_LIST)
        )
        return [CallView.model_validate(row) for row in rows]

    async def authorize(self, session: AsyncSession, principal: Principal, selection: ToolSelection) -> McpServer:
        require_capability(principal, 'mcp', 'use')
        row = await self._server(session, selection.server_id)
        self._version(row, selection.version)
        roles = await self.identity.active_role_ids(session, principal.id)
        if not row.enabled or not any(
            tool.name == selection.name and self._granted(principal, tool, roles) for tool in self._tools(row)
        ):
            raise AppError('mcp_tool_forbidden', '此 MCP 工具已停用或你没有使用权限', 403)
        self.transport.allowed(row.endpoint)
        return row

    async def execute(self, principal: Principal, selection: BoundTool, run_id: UUID) -> str:
        async with self.database.sessions() as session:
            current = await self.identity.principal_for_user(session, principal.id)
            row = await self.authorize(session, current, selection)
            endpoint, token = row.endpoint, self._token(row)
            tool = next(item for item in self._tools(row) if item.name == selection.name)
            definition = ToolDefinition.model_validate(tool.model_dump(exclude={'enabled', 'role_ids'}))
        began = monotonic()
        status = 'failed'
        error_code: str | None = 'mcp_interrupted'
        try:
            result = await self.transport.call(endpoint, token, definition, selection.arguments)
            async with self.database.sessions() as session:
                current = await self.identity.principal_for_user(session, principal.id)
                await self.authorize(session, current, selection)
            status, error_code = 'succeeded', None
            return result
        except AppError as caught:
            error_code = caught.code
            raise
        except asyncio.CancelledError:
            status, error_code = 'cancelled', 'mcp_cancelled'
            raise
        finally:
            async with self.database.sessions.begin() as session:
                session.add(
                    McpCall(
                        id=uuid4(),
                        server_id=selection.server_id,
                        caller_id=principal.id,
                        run_id=run_id,
                        tool_name=selection.name,
                        status=status,
                        error_code=error_code,
                        duration_ms=round((monotonic() - began) * 1000),
                        created_at=utcnow(),
                    )
                )

    @staticmethod
    def _granted(principal: Principal, tool: ManagedTool, roles: set[UUID]) -> bool:
        return tool.enabled and tool.read_only and (principal.is_admin or not roles.isdisjoint(tool.role_ids))

    @staticmethod
    def _tools(row: McpServer) -> list[ManagedTool]:
        return [ManagedTool.model_validate(item) for item in row.tools]

    @classmethod
    def _view(cls, row: McpServer, principal: Principal) -> ServerView:
        can_configure = has_capability(principal, 'mcp', 'configure')
        can_grant = has_capability(principal, 'mcp', 'grant')
        can_review = can_configure or can_grant or has_capability(principal, 'mcp', 'publish')
        tools = cls._tools(row) if can_review else []
        if not can_grant:
            for tool in tools:
                tool.role_ids = []
        return ServerView(
            id=row.id,
            name=row.name,
            endpoint=row.endpoint if can_configure else None,
            enabled=row.enabled,
            has_token=(row.encrypted_token is not None) if can_configure else None,
            version=row.version,
            synced_at=row.synced_at,
            tools=tools,
        )

    @staticmethod
    async def _server(session: AsyncSession, server_id: UUID, *, lock: bool = False) -> McpServer:
        query = select(McpServer).where(McpServer.id == server_id)
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise AppError('mcp_server_missing', 'MCP 服务不存在', 404)
        return row

    @staticmethod
    def _version(row: McpServer, expected: int) -> None:
        if row.version != expected:
            raise AppError('mcp_version_conflict', 'MCP 配置已变化，请刷新后重试', 409)

    def _encrypt(self, body: ServerCreate) -> str | None:
        if body.token is None:
            return None
        token = body.token.get_secret_value()
        if not token or not token.isascii() or not token.isprintable() or ' ' in token:
            raise AppError('mcp_token_invalid', '请输入合法的 Bearer Token', 422)
        return self.cipher.encrypt(token)

    def _token(self, row: McpServer) -> str | None:
        return self.cipher.decrypt(row.encrypted_token) if row.encrypted_token else None
