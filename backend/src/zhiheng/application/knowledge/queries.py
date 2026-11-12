"""Application-scoped KB collections apply current domain/source ACLs before pagination."""

from datetime import datetime
from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import FileInfo
from zhiheng.modules.knowledge.public import BaseInfo, DocumentInfo, FolderInfo, KnowledgeService
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from zhiheng.application.knowledge.documents import KnowledgeDocuments


class KnowledgeQueries:
    def __init__(self, database: Database, knowledge: KnowledgeService, access: ResourceAccessService) -> None:
        self.database, self.knowledge, self.access = database, knowledge, access
        self.document_workflows: KnowledgeDocuments | None = None

    async def get(self, caller: CallerContext, base_id: UUID) -> BaseInfo:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'knowledge_base', base_id, 'read', lock=False)
            return await self.knowledge.detail(session, self.access.personal(caller.principal), base_id)

    async def page(self, caller: CallerContext, after: tuple[datetime, UUID] | None, limit: int) -> list[BaseInfo]:
        async with self.database.sessions.begin() as session:
            caller = await self.access.current(session, caller, 'knowledge:read')
            assert caller.identity is not None
            grants = await self.access.grants.list(
                session, caller.principal.id, caller.identity.application_id, 'knowledge_base'
            )
            items = []
            for grant in grants:
                if 'read' not in grant.actions:
                    continue
                try:
                    await self.access.require(session, caller, 'knowledge_base', grant.resource_id, 'read', lock=False)
                    item = await self.knowledge.detail(
                        session, self.access.personal(caller.principal), grant.resource_id
                    )
                except AppError as error:
                    if error.status_code != HTTPStatus.NOT_FOUND:
                        raise
                    continue
                if after is None or (item.created_at, item.id) < after:
                    items.append(item)
            return sorted(items, key=lambda item: (item.created_at, item.id), reverse=True)[:limit]

    async def folders(self, caller: CallerContext, base_id: UUID) -> list[FolderInfo]:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'knowledge_base', base_id, 'read', lock=False)
            return await self.knowledge.folders(session, self.access.personal(caller.principal), base_id)

    async def documents(self, caller: CallerContext, base_id: UUID) -> list[DocumentInfo]:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'knowledge_base', base_id, 'read', lock=False)
            return (await self.knowledge.documents(session, self.access.personal(caller.principal), base_id)).items

    async def download(self, caller: CallerContext, base_id: UUID, document_id: UUID) -> tuple[FileInfo, bytes]:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'knowledge_base', base_id, 'read', lock=False)
            principal = self.access.personal(caller.principal)
            file = await self.knowledge.require_document_source(session, principal, base_id, document_id)
            ticket = await self.knowledge.files.read_ticket(session, principal, file.id)
        content = await self.knowledge.files.read_content(ticket)
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'knowledge_base', base_id, 'read', lock=False)
            await self.knowledge.require_document_source(session, principal, base_id, document_id)
        return file, content

    async def preview(self, caller: CallerContext, base_id: UUID, document_id: UUID) -> dict[str, object]:
        assert self.document_workflows is not None
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'knowledge_base', base_id, 'read', lock=False)
        result = await self.document_workflows.preview(self.access.personal(caller.principal), base_id, document_id)
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'knowledge_base', base_id, 'read', lock=False)
            await self.knowledge.require_document_source(
                session, self.access.personal(caller.principal), base_id, document_id
            )
        return result
