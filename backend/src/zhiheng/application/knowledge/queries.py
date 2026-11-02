"""Application-scoped KB collections apply current domain/source ACLs before pagination."""

from datetime import datetime
from http import HTTPStatus
from uuid import UUID

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.infrastructure.database import Database
from zhiheng.modules.knowledge.public import BaseInfo, DocumentInfo, FolderInfo, KnowledgeService
from zhiheng.shared.errors import AppError


class KnowledgeQueries:
    def __init__(self, database: Database, knowledge: KnowledgeService, access: ResourceAccessService) -> None:
        self.database, self.knowledge, self.access = database, knowledge, access

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
