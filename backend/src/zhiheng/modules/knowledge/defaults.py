"""Versioned platform defaults never replace the model bound to an existing index."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.knowledge.orm import KnowledgeDefaults
from zhiheng.modules.models.public import ModelService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


class KnowledgeDefaultsInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    default_embed_model_id: UUID | None
    default_rerank_model_id: UUID | None
    chunk_size: int
    chunk_overlap: int
    version: int
    updated_at: datetime


class KnowledgeDefaultsPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=1)
    default_embed_model_id: UUID | None = None
    default_rerank_model_id: UUID | None = None
    chunk_size: int | None = Field(default=None, strict=True, ge=100, le=4000)
    chunk_overlap: int | None = Field(default=None, strict=True, ge=0, le=1000)

    @model_validator(mode='after')
    def nonnull_parameters(self) -> 'KnowledgeDefaultsPatch':
        if any(
            name in self.model_fields_set and getattr(self, name) is None for name in ('chunk_size', 'chunk_overlap')
        ):
            raise ValueError('Chunk parameters cannot be null')
        return self


class KnowledgeDefaultsService:
    def __init__(self, models: ModelService) -> None:
        self.models = models

    async def get(self, session: AsyncSession, principal: Principal) -> KnowledgeDefaultsInfo:
        principal.require_permission('system:configs:read')
        row = await session.get(KnowledgeDefaults, 1)
        if row is None:
            raise AppError('knowledge_defaults_missing', '知识服务配置尚未初始化', 409)
        return KnowledgeDefaultsInfo.model_validate(row)

    async def patch(
        self, session: AsyncSession, principal: Principal, body: KnowledgeDefaultsPatch
    ) -> KnowledgeDefaultsInfo:
        principal.require_permission('system:configs:write')
        await session.execute(insert(KnowledgeDefaults).values(id=1).on_conflict_do_nothing())
        row = await session.get(KnowledgeDefaults, 1, with_for_update=True, populate_existing=True)
        assert row is not None
        if row.version != body.expected_version:
            raise AppError('knowledge_settings_conflict', '知识服务配置已变化，请刷新后重试', 409)
        for field, model_type in (('default_embed_model_id', 'embedding'), ('default_rerank_model_id', 'rerank')):
            value = getattr(body, field)
            if field in body.model_fields_set and value is not None:
                binding = await self.models.resolve(session, value)
                if binding.model_type != model_type:
                    raise AppError('invalid_knowledge_model', '请选择对应类型的已启用模型', 422)
        ranker_id = (
            body.default_rerank_model_id
            if 'default_rerank_model_id' in body.model_fields_set
            else row.default_rerank_model_id
        )
        if ranker_id is None:
            raise AppError('knowledge_rerank_required', '必须配置默认重排模型，不能清空', 422)
        ranker = await self.models.resolve(session, ranker_id)
        if ranker.model_type != 'rerank':
            raise AppError('invalid_knowledge_model', '请选择已启用的重排模型', 422)
        size = body.chunk_size if body.chunk_size is not None else row.chunk_size
        overlap = body.chunk_overlap if body.chunk_overlap is not None else row.chunk_overlap
        if overlap >= size:
            raise AppError('invalid_chunks', '重叠大小必须小于切片大小', 422)
        for field in body.model_fields_set - {'expected_version'}:
            setattr(row, field, getattr(body, field))
        row.version += 1
        row.updated_at = utcnow()
        await session.flush()
        return KnowledgeDefaultsInfo.model_validate(row)
