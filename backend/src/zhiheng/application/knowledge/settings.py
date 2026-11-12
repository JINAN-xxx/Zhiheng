"""Owner-controlled knowledge configuration and explicit atomic index replacement."""

from uuid import UUID

from zhiheng.infrastructure.database import Database
from zhiheng.modules.billing.public import lock_billing_admission
from zhiheng.modules.knowledge.public import (
    BaseInfo,
    JobInfo,
    KnowledgeService,
    KnowledgeSettings,
    fingerprint,
)
from zhiheng.modules.models.public import ModelService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class KnowledgeConfiguration:
    def __init__(self, database: Database, knowledge: KnowledgeService, models: ModelService) -> None:
        self.database, self.knowledge, self.models = database, knowledge, models

    async def save(
        self, principal: Principal, base_id: UUID, body: KnowledgeSettings
    ) -> tuple[BaseInfo, JobInfo | None]:
        value = fingerprint(['knowledge_settings', str(base_id), body.model_dump(mode='json')])
        async with self.database.sessions.begin() as session:
            await lock_billing_admission(session)
            existing = await self.knowledge.existing_job(session, principal, body.client_request_id, value)
            base = await self.knowledge.authorize_base(session, principal, base_id, manage=True)
            if base.owner_id != principal.id:
                raise AppError('knowledge_not_found', '知识库不存在', 404)
            if existing:
                return await self.knowledge.detail(session, principal, base_id), existing
            ranker_id = await self.knowledge.required_rerank_id(
                session, use_default=body.use_default_rerank, model_id=body.rerank_model_id
            )
            identifiers = {body.embed_model_id, ranker_id}
            bindings = await self.models.resolve_many(session, sorted(identifiers))
            binding = bindings[body.embed_model_id]
            if binding.model_type != 'embedding' or bindings[ranker_id].model_type != 'rerank':
                raise AppError('invalid_knowledge_model', '请选择对应类型的已启用模型', 422)
            return await self.knowledge.configure(session, principal, base_id, body, binding, value)
