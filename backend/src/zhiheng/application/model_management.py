"""Compose lifecycle reference checks through public owners in one transaction."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.developer.public import KeyService
from zhiheng.modules.models.public import ConnectionInfo, ModelInfo, ModelService
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.principal import Principal


class ModelManagementService:
    def __init__(
        self,
        models: ModelService,
        runs: RunStore,
        keys: KeyService,
        agents: AgentService,
        work: WorkService | None = None,
    ) -> None:
        self._work = work or WorkService()
        self._models, self._runs, self._keys, self._agents = models, runs, keys, agents

    async def delete_connection(
        self, session: AsyncSession, principal: Principal, connection_id: UUID
    ) -> ConnectionInfo:
        # The model owner holds the same connection row lock as resolve() while
        # this runtime-owned query checks every historical frozen binding.
        return await self._models.delete_connection(
            session, principal, connection_id, reference_check=self._runs.references_connection
        )

    async def delete_model(self, session: AsyncSession, principal: Principal, model_id: UUID) -> ModelInfo:
        return await self._models.delete_model(session, principal, model_id, reference_check=self._model_referenced)

    async def _model_referenced(self, session: AsyncSession, model_id: UUID) -> bool:
        return (
            await self._work.references_model(session, model_id)
            or await self._agents.references_model(session, model_id)
            or await self._keys.references_model(session, model_id)
            or await self._runs.references_model(session, model_id)
        )
