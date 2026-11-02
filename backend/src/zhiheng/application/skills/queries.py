"""Current personal/App visibility for native skill metadata and immutable packages."""

from datetime import datetime
from uuid import UUID

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.infrastructure.database import Database
from zhiheng.modules.skills.public import ResourceSkillInfo, RevisionContent, RevisionInfo, SkillService


class SkillQueries:
    def __init__(self, database: Database, skills: SkillService, access: ResourceAccessService) -> None:
        self.database, self.skills, self.access = database, skills, access

    async def get(self, caller: CallerContext, skill_id: UUID) -> ResourceSkillInfo:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'skill', skill_id, 'read')
            return await self.skills.resource_info(session, caller.principal.id, skill_id)

    async def page(
        self, caller: CallerContext, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[ResourceSkillInfo]:
        async with self.database.sessions.begin() as session:
            caller = await self.access.current(session, caller, 'skills:read')
            assert caller.identity is not None
            grants = await self.access.grants.list(
                session, caller.principal.id, caller.identity.application_id, 'skill'
            )
            ids = [
                g.resource_id
                for g in grants
                if 'read' in g.actions
                and g.ownership.owner_id == caller.principal.id
                and g.ownership.stamp == str(caller.principal.id)
            ]
            return await self.skills.resource_items(session, caller.principal.id, ids, after=after, limit=limit)

    async def versions(
        self, caller: CallerContext, skill_id: UUID, *, after: tuple[datetime, UUID] | None, limit: int
    ) -> list[RevisionInfo]:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'skill', skill_id, 'read')
            return await self.skills.revisions(session, caller.principal.id, skill_id, after=after, limit=limit)

    async def revision(self, caller: CallerContext, skill_id: UUID, revision_id: UUID) -> RevisionInfo:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'skill', skill_id, 'read')
            return await self.skills.revision(session, caller.principal.id, skill_id, revision_id)

    async def content(self, caller: CallerContext, skill_id: UUID, revision_id: UUID) -> RevisionContent:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'skill', skill_id, 'read')
            return await self.skills.revision_content(session, caller.principal.id, skill_id, revision_id)
