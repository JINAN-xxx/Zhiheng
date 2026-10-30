"""Compose lifecycle reference checks through public owners in one transaction."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.developer.public import KeyService
from zhiheng.modules.knowledge.public import KnowledgeService
from zhiheng.modules.models.public import ConnectionInfo, ModelInfo, ModelService
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import reference_conflict


class ModelManagementService:
    def __init__(
        self,
        models: ModelService,
        runs: RunStore,
        keys: KeyService,
        agents: AgentService,
        work: WorkService | None = None,
        knowledge: KnowledgeService | None = None,
    ) -> None:
        self._work = work or WorkService()
        self._models, self._runs, self._keys, self._agents = models, runs, keys, agents
        self._knowledge = knowledge

    async def delete_connection(
        self, session: AsyncSession, principal: Principal, connection_id: UUID
    ) -> ConnectionInfo:
        async def referenced(session: AsyncSession, resource_id: UUID) -> bool:
            references = await self._runs.connection_references(session, resource_id)
            if self._knowledge:
                references.extend(await self._knowledge.connection_references(session, resource_id))
            if references:
                raise reference_conflict('connection_referenced', '连接仍被进行中的任务引用', principal, references)
            return False

        return await self._models.delete_connection(session, principal, connection_id, reference_check=referenced)

    async def delete_model(self, session: AsyncSession, principal: Principal, model_id: UUID) -> ModelInfo:
        async def referenced(session: AsyncSession, resource_id: UUID) -> bool:
            references = await self._work.model_references(session, resource_id)
            references.extend(await self._agents.model_references(session, resource_id))
            references.extend(await self._keys.model_references(session, resource_id))
            references.extend(await self._runs.model_references(session, resource_id))
            if self._knowledge:
                references.extend(await self._knowledge.model_references(session, resource_id))
            if references:
                raise reference_conflict(
                    'model_referenced', '模型仍被当前资源或进行中的任务引用', principal, references
                )
            return False

        return await self._models.delete_model(session, principal, model_id, reference_check=referenced)
