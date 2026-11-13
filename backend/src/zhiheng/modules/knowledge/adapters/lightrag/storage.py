"""Registered composition wrappers for SDK final KV/status implementations."""

from dataclasses import dataclass
from typing import Any, ClassVar

from lightrag.base import BaseKVStorage, DocStatusStorage  # type: ignore[import-untyped]
from lightrag.kg.pgtable_impl import PGTableGraphStorage  # type: ignore[import-untyped]
from lightrag.kg.postgres_impl import PGDocStatusStorage, PGKVStorage  # type: ignore[import-untyped]
from zhiheng.modules.knowledge.adapters.lightrag.postgres import CURRENT_STORAGE, PostgresContext
from zhiheng.shared.errors import AppError


class _Composition:
    supports_strict_point_reads: ClassVar[bool] = True
    delegate: Any
    owner: PostgresContext
    workspace: str

    async def initialize(self) -> None:
        self.owner = CURRENT_STORAGE.get()
        if self.workspace != self.owner.workspace:
            raise AppError('graph_namespace_mismatch', '图谱存储空间不匹配', 503)
        self.delegate.db = await self.owner.acquire(self)
        await self.delegate.initialize()
        if self.delegate.workspace != self.workspace:
            raise AppError('graph_namespace_mismatch', '图谱存储空间不匹配', 503)

    async def finalize(self) -> None:
        if self.delegate.db is not None:
            self.owner.release(self)
            self.delegate.db = None

    async def drop(self) -> dict[str, str]:
        raise AppError('graph_drop_denied', '图谱存储只能按受管版本回收', 409)

    async def index_done_callback(self) -> None:
        await self.delegate.index_done_callback()


@dataclass
class GraphKVStorage(_Composition, BaseKVStorage):  # type: ignore[misc]
    def __post_init__(self) -> None:
        self.delegate = PGKVStorage(self.namespace, self.workspace, self.global_config, self.embedding_func)

    async def get_by_id(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_by_id(*args, **kwargs)

    async def get_by_id_strict(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_by_id_strict(*args, **kwargs)

    async def get_by_ids(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_by_ids(*args, **kwargs)

    async def filter_keys(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.filter_keys(*args, **kwargs)

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await self.delegate.upsert(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await self.delegate.delete(*args, **kwargs)

    async def is_empty(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.is_empty(*args, **kwargs)


@dataclass
class GraphDocStorage(_Composition, DocStatusStorage):  # type: ignore[misc]
    def __post_init__(self) -> None:
        self.delegate = PGDocStatusStorage(self.namespace, self.workspace, self.global_config, self.embedding_func)

    async def get_by_id(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_by_id(*args, **kwargs)

    async def get_by_id_strict(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_by_id_strict(*args, **kwargs)

    async def get_by_ids(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_by_ids(*args, **kwargs)

    async def filter_keys(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.filter_keys(*args, **kwargs)

    async def upsert(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await self.delegate.upsert(*args, **kwargs)

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await self.delegate.delete(*args, **kwargs)

    async def is_empty(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.is_empty(*args, **kwargs)

    async def get_status_counts(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_status_counts(*args, **kwargs)

    async def get_docs_by_statuses(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_docs_by_statuses(*args, **kwargs)

    async def get_docs_by_track_id(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_docs_by_track_id(*args, **kwargs)

    async def get_docs_paginated(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_docs_paginated(*args, **kwargs)

    async def get_all_status_counts(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_all_status_counts(*args, **kwargs)

    async def get_doc_by_file_path(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_doc_by_file_path(*args, **kwargs)

    async def get_doc_by_file_basename(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_doc_by_file_basename(*args, **kwargs)

    async def get_doc_by_content_hash(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_doc_by_content_hash(*args, **kwargs)

    async def get_docs_by_statuses_page(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_docs_by_statuses_page(*args, **kwargs)

    async def get_docs_by_ids(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_docs_by_ids(*args, **kwargs)

    async def get_full_docs_by_ids(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.get_full_docs_by_ids(*args, **kwargs)

    async def count_docs_by_statuses(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.count_docs_by_statuses(*args, **kwargs)

    async def update_doc_status_fields(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.write()
        return await self.delegate.update_doc_status_fields(*args, **kwargs)

    async def resolve_doc_source_strict(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.resolve_doc_source_strict(*args, **kwargs)

    async def list_source_conflicts_page(self, *args: Any, **kwargs: Any) -> Any:
        self.owner.check()
        return await self.delegate.list_source_conflicts_page(*args, **kwargs)

    async def repair_source_conflict(self, *args: Any, **kwargs: Any) -> Any:
        raise AppError('graph_repair_denied', '运行连接禁止修复来源', 409)


class GraphTableStorage(PGTableGraphStorage):  # type: ignore[misc]
    db: Any

    async def initialize(self) -> None:
        self.owner = CURRENT_STORAGE.get()
        if self.workspace != self.owner.workspace:
            raise AppError('graph_namespace_mismatch', '图谱存储空间不匹配', 503)
        self.db = await self.owner.acquire(self)

    async def finalize(self) -> None:
        if self.db is not None:
            self.owner.release(self)
            self.db = None

    async def drop(self) -> dict[str, str]:
        raise AppError('graph_drop_denied', '图谱存储只能按受管版本回收', 409)
