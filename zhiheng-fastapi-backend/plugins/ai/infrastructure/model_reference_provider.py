"""装配目录删除需要的各模块公共查询，不读取其他插件的内部实现。"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from exceptions.exception import ServiceException
from plugins.ai.knowledge.metadata import MetadataStore
from plugins.ai.knowledge.model_references import MODEL_REFERENCE_LOCK
from plugins.ai.knowledge.model_references import has_model_references as has_knowledge_model_references
from plugins.ai.knowledge.models import KnowledgeError
from plugins.ai.public.model_references import MODEL_REFERENCE_CHECK_UNAVAILABLE, has_native_model_references
from plugins.core.discovery.registry import PluginRegistry
from plugins.core.runtime.resource_references import has_resource_references


class ModelDeletionReferences:
    def __init__(self, db: AsyncSession, *, registry: PluginRegistry | None, metadata: MetadataStore) -> None:
        self._db = db
        self._registry = registry
        self._metadata = metadata

    @asynccontextmanager
    async def deletion_guard(self) -> AsyncIterator[None]:
        """先取得知识库引用写锁，再锁 SQL 模型，保持到删除事务提交结束。"""
        try:
            async with self._metadata.lock(MODEL_REFERENCE_LOCK):
                yield
        except (KnowledgeError, OSError):
            raise ServiceException(message=MODEL_REFERENCE_CHECK_UNAVAILABLE) from None

    async def has_model_references(self, model_id: str) -> bool:
        try:
            native = await has_native_model_references(self._db, model_id)
            extensions = await has_resource_references(self._registry, self._db, 'ai.model', model_id)
            knowledge = await has_knowledge_model_references(self._metadata, model_id)
            return native or extensions or knowledge
        except Exception:
            raise ServiceException(message=MODEL_REFERENCE_CHECK_UNAVAILABLE) from None


def get_model_reference_checker(request: Request, db: AsyncSession) -> ModelDeletionReferences:
    runtime = getattr(request.app.state, 'knowledge_runtime', None)
    data_dir = getattr(runtime, 'data_dir', None) or Path(os.getenv('KNOWLEDGE_DATA_DIR', 'data/knowledge')).resolve()
    return ModelDeletionReferences(
        db, registry=getattr(request.app.state, 'plugin_registry', None), metadata=MetadataStore(Path(data_dir))
    )
