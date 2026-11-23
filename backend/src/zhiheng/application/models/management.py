"""Compose lifecycle reference checks through public owners in one transaction."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.execution.references import RunReferences
from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.developer.public import KeyService
from zhiheng.modules.knowledge.public import KnowledgeService
from zhiheng.modules.models.public import ConnectionInfo, ModelInfo, ModelService
from zhiheng.modules.work.public import WorkService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import reference_conflict

KEY_REFERENCE_SAMPLE = 20


class ModelManagementService:
    def __init__(
        self,
        models: ModelService,
        runs: RunReferences,
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
                references.extend(await self._knowledge.references.connection_references(session, resource_id))
            if references:
                raise reference_conflict('connection_referenced', '连接仍被进行中的任务引用', principal, references)
            return False

        return await self._models.connections.remove(session, principal, connection_id, reference_check=referenced)

    async def delete_model(
        self, session: AsyncSession, principal: Principal, model_id: UUID, *, retire_key_access: bool = False
    ) -> ModelInfo:
        if retire_key_access:
            principal.require_admin()

        async def referenced(session: AsyncSession, resource_id: UUID) -> bool:
            references = await self._work.model_references(session, resource_id)
            references.extend(await self._agents.model_references(session, resource_id))
            references.extend(await self._runs.model_references(session, resource_id))
            if self._knowledge:
                references.extend(await self._knowledge.references.model_references(session, resource_id))
            # Deletion makes the model unavailable to every key without rewriting historical grants.
            key_refs = [] if retire_key_access else await self._keys.model_references(session, resource_id)
            if references or key_refs:
                if not references and principal.is_admin:
                    conflict = reference_conflict(
                        'model_referenced', '模型仍被 API 密钥授权使用', principal, key_refs[:KEY_REFERENCE_SAMPLE]
                    )
                    raise AppError(
                        conflict.code,
                        conflict.message,
                        conflict.status_code,
                        details={**(conflict.details or {}), 'retirable_key_count': len(key_refs)},
                    )
                raise reference_conflict(
                    'model_referenced',
                    '模型仍被当前资源或进行中的任务引用',
                    principal,
                    [*references, *key_refs[:KEY_REFERENCE_SAMPLE]],
                )
            return False

        return await self._models.catalog.remove(session, principal, model_id, reference_check=referenced)
