from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import AgentConfig, AgentInfo, AgentService, AgentSummary, AgentUpdate
from zhiheng.modules.developer.public import ApplicationService
from zhiheng.modules.models.public import ModelBinding, ModelService
from zhiheng.modules.skills.public import SkillSelection, SkillService, resolve_selection
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from zhiheng.application.knowledge_preparation import KnowledgePreparation


def owner(agent: AgentInfo) -> Principal:
    return Principal(agent.owner_id, '', False)


class AgentManagementService:
    def __init__(self, agents: AgentService, applications: ApplicationService, models: ModelService) -> None:
        self._agents, self._applications, self._models = agents, applications, models
        self.knowledge: KnowledgePreparation | None = None

    async def validate(
        self, session: AsyncSession, principal: Principal, config: AgentConfig, *, publish: bool = False
    ) -> ModelBinding:
        # All binding writers use application -> model -> connection -> agent -> skills.
        if config.application_id is not None:
            if publish:
                await self._applications.require_active_owned(session, principal.id, config.application_id)
            else:
                await self._applications.require_owned(session, principal.id, config.application_id)
        elif publish:
            raise AppError('agent_application_required', '发布智能体前请绑定应用', 422)
        binding = (
            await self.knowledge.binding_for(session, principal, config.model_id, config.knowledge_base_ids)
            if config.knowledge_base_ids and self.knowledge
            else await self._models.resolve(session, config.model_id)
        )
        if binding.model_type != 'chat':
            raise AppError('model_unavailable', '智能体仅支持聊天模型', 422)
        if config.max_tokens > binding.max_output_tokens:
            raise AppError('agent_token_limit', '输出长度超过所选模型限制', 422)
        return binding

    async def create(self, session: AsyncSession, principal: Principal, config: AgentConfig) -> AgentInfo:
        await self.validate(session, Principal(principal.id, '', False), config)
        await resolve_selection(session, config.skill_slugs)
        return await self._agents.create(session, principal, config)

    async def update(self, session: AsyncSession, principal: Principal, agent_id: UUID, body: AgentUpdate) -> AgentInfo:
        agent = await self._agents.get(session, principal, agent_id, expected_version=body.expected_version)
        config = AgentConfig.model_validate(body.model_dump(exclude={'expected_version'}))
        await self.validate(session, owner(agent), config)
        await self._agents.get(session, principal, agent_id, lock=True, expected_version=body.expected_version)
        await resolve_selection(session, config.skill_slugs)
        return await self._agents.update(session, principal, agent_id, config, body.expected_version)

    async def publish(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, expected_version: int
    ) -> AgentInfo:
        agent = await self._agents.get(session, principal, agent_id, expected_version=expected_version)
        config = AgentConfig.model_validate(agent.model_dump(include=set(AgentConfig.model_fields)))
        await self.validate(session, owner(agent), config, publish=True)
        await self._agents.get(session, principal, agent_id, lock=True, expected_version=expected_version)
        selection: SkillSelection = await resolve_selection(session, config.skill_slugs)
        return await self._agents.publish(session, principal, agent_id, expected_version, selection)

    async def available(self, session: AsyncSession, principal: Principal) -> list[AgentSummary]:
        # A directory is a read: do not accumulate row locks across unrelated owners/models.
        models = {row.id: row for row in await self._models.list_models(session) if row.model_type == 'chat'}
        skills = {row.slug for row in await SkillService().available(session)}
        result = []
        for summary in await self._agents.summaries(session, principal):
            agent = await self._agents.get(session, principal, summary.id)
            config = agent.published_config
            assert config is not None
            if (
                config.model_id not in models
                or config.max_tokens > models[config.model_id].max_output_tokens
                or not set(config.skill_slugs) <= skills
                or config.application_id is None
            ):
                continue
            try:
                if (
                    config.knowledge_base_ids
                    and self.knowledge
                    and not await self.knowledge.can_use(session, agent.owner_id, config.knowledge_base_ids)
                ):
                    continue
                await self._applications.require_active_owned(
                    session, agent.owner_id, config.application_id, lock=False
                )
            except AppError:
                continue
            result.append(summary)
        return result
