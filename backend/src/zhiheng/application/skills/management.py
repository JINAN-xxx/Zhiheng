"""Skill deletion checks current dependencies while holding the admission lock."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.knowledge.public import KnowledgeService
from zhiheng.modules.skills.public import SkillInfo, SkillService
from zhiheng.modules.work.public import WorkService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import reference_conflict


class PreparationSkillOwners:
    """Resolve persisted preparation ownership through each domain's public metadata."""

    def __init__(self, agents: AgentService, work: WorkService) -> None:
        self.agents, self.work = agents, work

    async def resolve(
        self,
        session: AsyncSession,
        resource_type: str,
        resource_id: UUID,
        request: dict[str, object],
    ) -> UUID | None:
        if resource_type in {'agent_preview', 'agent_invocation'}:
            return await self.agents.resource_owner(session, resource_id)
        body = request.get('body') or request.get('admission_body')
        if resource_type != 'work_turn' or not isinstance(body, dict):
            return None
        try:
            agent_id = UUID(str(body.get('agent_id')))
        except ValueError:
            return None
        try:
            member = await self.work.member(session, resource_id, agent_id)
        except AppError as error:
            if error.code == 'work_member_not_found':
                return None
            raise
        return member.agent_owner_id


class SkillManagementService:
    def __init__(
        self,
        skills: SkillService,
        agents: AgentService,
        work: WorkService | None = None,
        knowledge: KnowledgeService | None = None,
        chat: ChatService | None = None,
    ) -> None:
        self._work = work or WorkService()
        self._skills, self._agents = skills, agents
        self._knowledge = knowledge
        self._chat = chat or ChatService()

    async def remove(self, session: AsyncSession, principal: Principal, skill_id: UUID) -> SkillInfo:
        skill_ids = await self._skills.reference_ids(session, principal.id, skill_id)

        async def referenced(locked_session: AsyncSession, slug: str) -> bool:
            references = [
                *await self._work.skill_references(locked_session, slug, owner_id=principal.id, skill_ids=skill_ids),
                *await self._agents.skill_references(locked_session, slug, owner_id=principal.id, skill_ids=skill_ids),
                *await self._chat.skill_references(locked_session, slug, owner_id=principal.id, skill_ids=skill_ids),
            ]
            if self._knowledge is not None:
                references.extend(
                    await self._knowledge.skill_references(
                        locked_session, slug, owner_id=principal.id, skill_ids=skill_ids
                    )
                )
            if references:
                raise reference_conflict('skill_referenced', '技能仍有有效引用或进行中的调用', principal, references)
            return False

        return await self._skills.remove(session, principal, skill_id, reference_check=referenced)
