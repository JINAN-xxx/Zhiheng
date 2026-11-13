from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.execution.references import RunReferences
from zhiheng.modules.agents.public import (
    AgentConfig,
    AgentInfo,
    AgentService,
    AgentSummary,
    AgentUpdate,
    AssistantConversations,
)
from zhiheng.modules.developer.public import ApplicationService
from zhiheng.modules.knowledge.public import KnowledgeService, RerankPolicy
from zhiheng.modules.models.public import ModelBinding, ModelService
from zhiheng.modules.skills.public import SkillSelection, SkillService, resolve_selection
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import reference_conflict

if TYPE_CHECKING:
    from zhiheng.application.knowledge.preparation import KnowledgePreparation


def owner(agent: AgentInfo) -> Principal:
    return Principal(agent.owner_id, '', False)


class AgentManagementService:
    def __init__(
        self,
        agents: AgentService,
        applications: ApplicationService,
        models: ModelService,
        work: WorkService | None = None,
        runs: RunReferences | None = None,
        knowledge_resources: KnowledgeService | None = None,
    ) -> None:
        self._agents, self._applications, self._models = agents, applications, models
        self._work, self._runs, self._knowledge_resources = (
            work or WorkService(),
            runs or RunReferences(RunStore()),
            knowledge_resources,
        )
        self.knowledge: KnowledgePreparation | None = None

    async def knowledge_policy(self, session: AsyncSession, principal: Principal, base_ids: list[UUID]) -> RerankPolicy:
        require_capability(principal, 'knowledge', 'read')
        assert self.knowledge is not None
        for base_id in sorted(base_ids):
            await self.knowledge.knowledge.authorize_base(session, principal, base_id)
        return await self.knowledge.knowledge.retrieval_rerank_policy(session, base_ids)

    async def remove(self, session: AsyncSession, principal: Principal, agent_id: UUID) -> AgentInfo:
        async def referenced(current: AsyncSession, target: UUID) -> bool:
            references = await self._work.agent_references(current, target)
            references.extend(await self._runs.agent_references(current, target))
            conversation_ids = await AssistantConversations().conversation_ids(current, target)
            references.extend(await self._runs.assistant_references(current, target, conversation_ids))
            if self._knowledge_resources is not None:
                references.extend(await self._knowledge_resources.agent_references(current, target))
            if references:
                raise reference_conflict(
                    'agent_referenced', '智能体仍被当前任务使用，请先解除引用', principal, references
                )
            return False

        return await self._agents.remove(session, principal, agent_id, reference_check=referenced)

    async def validate(
        self, session: AsyncSession, principal: Principal, config: AgentConfig, *, publish: bool = False
    ) -> ModelBinding:
        # All binding writers use application -> model -> connection -> agent -> skills.
        if config.application_id is not None:
            if publish:
                await self._applications.require_active_owned(session, principal.id, config.application_id)
            else:
                await self._applications.require_owned(session, principal.id, config.application_id)
        binding = (
            await self.knowledge.binding_for(session, principal, config.model_id, config.knowledge_base_ids)
            if config.knowledge_base_ids and self.knowledge
            else await self._models.resolve(session, config.model_id)
        )
        if binding.model_type != 'chat':
            raise AppError('model_unavailable', '智能体仅支持聊天模型', 422)
        if config.max_tokens > binding.max_output_tokens:
            raise AppError('agent_token_limit', '输出长度超过所选模型限制', 422)
        if config.knowledge_base_ids and self.knowledge:
            await self.knowledge.knowledge.retrieval_rerank_binding(session, config.knowledge_base_ids)
        return binding

    async def create(
        self, session: AsyncSession, principal: Principal, config: AgentConfig, client_request_id: UUID | None = None
    ) -> AgentInfo:
        if client_request_id is not None:
            existing = await self._agents.existing_create(session, principal, client_request_id, config)
            if existing is not None:
                return existing
        await self.validate(session, Principal(principal.id, '', False), config)
        await resolve_selection(session, principal.id, config.skill_slugs)
        return await self._agents.create(session, principal, config, client_request_id=client_request_id)

    async def update(self, session: AsyncSession, principal: Principal, agent_id: UUID, body: AgentUpdate) -> AgentInfo:
        agent = await self._agents.get(session, principal, agent_id, expected_version=body.expected_version)
        config = AgentConfig.model_validate(body.model_dump(exclude={'expected_version'}))
        await self.validate(session, owner(agent), config)
        await self._agents.get(session, principal, agent_id, lock=True, expected_version=body.expected_version)
        await resolve_selection(session, agent.owner_id, config.skill_slugs)
        return await self._agents.update(session, principal, agent_id, config, body.expected_version)

    async def publish(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, expected_version: int
    ) -> AgentInfo:
        agent = await self._agents.get(session, principal, agent_id, expected_version=expected_version)
        config = AgentConfig.model_validate(agent.model_dump(include=set(AgentConfig.model_fields)))
        await self.validate(session, owner(agent), config, publish=True)
        await self._agents.get(session, principal, agent_id, lock=True, expected_version=expected_version)
        selection: SkillSelection = await resolve_selection(session, agent.owner_id, config.skill_slugs)
        return await self._agents.publish(session, principal, agent_id, expected_version, selection)

    async def available(self, session: AsyncSession, principal: Principal) -> list[AgentSummary]:
        # A directory is a read: do not accumulate row locks across unrelated owners/models.
        models = {row.id: row for row in await self._models.list_models(session) if row.model_type == 'chat'}
        result = []
        for summary in await self._agents.summaries(session, principal):
            agent = await self._agents.get(session, principal, summary.id)
            config = agent.published_config
            skills = {row.slug for row in await SkillService().available(session, agent.owner_id)}
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

    async def api_access(self, session: AsyncSession, principal: Principal, agent_id: UUID) -> dict[str, object]:
        agent = await self._agents.get(session, principal, agent_id)
        config = agent.published_config
        if config is None or config.application_id is None:
            raise AppError('agent_not_published', '智能体尚未发布', 409)
        actor = owner(agent)
        await self._applications.require_active_owned(session, actor.id, config.application_id, lock=False)
        model_ids = {config.model_id}
        if config.knowledge_base_ids:
            assert self.knowledge is not None
            bases = await self.knowledge.store.snapshot(session, actor, config.knowledge_base_ids)
            model_ids.update(base.binding.model_id for base in bases)
            ranker = await self.knowledge.knowledge.retrieval_rerank_binding(session, config.knowledge_base_ids)
            model_ids.add(ranker.model_id)
        return {
            'application_id': config.application_id,
            'published_version': agent.published_version,
            'model_ids': sorted(model_ids),
        }
