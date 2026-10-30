"""Skill deletion checks current dependencies while holding the admission lock."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.knowledge.public import KnowledgeService
from zhiheng.modules.skills.public import SkillInfo, SkillService
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import reference_conflict


class SkillManagementService:
    def __init__(
        self,
        skills: SkillService,
        agents: AgentService,
        work: WorkService | None = None,
        runs: RunStore | None = None,
        knowledge: KnowledgeService | None = None,
        chat: ChatService | None = None,
    ) -> None:
        self._work = work or WorkService()
        self._skills, self._agents = skills, agents
        self._runs = runs or RunStore()
        self._knowledge = knowledge
        self._chat = chat or ChatService()

    async def remove(self, session: AsyncSession, principal: Principal, skill_id: UUID) -> SkillInfo:
        async def referenced(locked_session: AsyncSession, slug: str) -> bool:
            references = [
                *await self._work.skill_references(locked_session, slug),
                *await self._agents.skill_references(locked_session, slug),
                *await self._runs.skill_references(locked_session, slug),
                *await self._chat.skill_references(locked_session, slug),
            ]
            if self._knowledge is not None:
                references.extend(await self._knowledge.skill_references(locked_session, slug))
            if references:
                raise reference_conflict('skill_referenced', '技能仍有有效引用或进行中的调用', principal, references)
            return False

        return await self._skills.remove(session, principal, skill_id, reference_check=referenced)
