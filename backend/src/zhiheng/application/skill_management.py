"""Skill deletion composes historical references through their public owner."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.skills.public import SkillInfo, SkillService
from zhiheng.modules.work.public import WorkService
from zhiheng.shared.principal import Principal


class SkillManagementService:
    def __init__(self, skills: SkillService, agents: AgentService, work: WorkService | None = None) -> None:
        self._work = work or WorkService()
        self._skills, self._agents = skills, agents

    async def remove(self, session: AsyncSession, principal: Principal, skill_id: UUID) -> SkillInfo:
        return await self._skills.remove(session, principal, skill_id, reference_check=self._referenced)

    async def _referenced(self, session: AsyncSession, slug: str) -> bool:
        return await self._work.references_skill(session, slug) or await self._agents.references_skill(session, slug)
