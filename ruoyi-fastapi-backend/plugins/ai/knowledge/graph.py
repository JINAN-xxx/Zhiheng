# ruff: noqa: PLC0415
# LightRAG and its optional dependencies load only when graph features are used.
"""LightRAG 1.5.7 with PostgreSQL graph/state and Milvus vectors.

Generated answers remain a management feature; Agent retrieval uses the parallel
vector index with verified document/chunk membership in KnowledgeRuntime.
"""

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from module_Knowledge.src.knowledge.kb_utils import normalize_embedding_text
from plugins.ai.domain.model_runtime import ModelMessage
from plugins.ai.knowledge.graph_tokenizer import load_graph_tokenizer
from plugins.ai.knowledge.models import KnowledgeError, QueryParameters, embedding_fingerprint
from plugins.ai.knowledge.stores import (
    MILVUS_REQUEST_TIMEOUT_SECONDS,
    _blocking,
    _wait_for_completion,
    configure_graph_environment,
    require_graph,
)

_STORAGE_NAMES = (
    'full_docs',
    'text_chunks',
    'full_entities',
    'full_relations',
    'entity_chunks',
    'relation_chunks',
    'entities_vdb',
    'relationships_vdb',
    'chunks_vdb',
    'chunk_entity_relation_graph',
    'llm_response_cache',
    'doc_status',
)
_EMBEDDING_TIMEOUT = 30
_LLM_TIMEOUT = 90
_INITIALIZATION_TIMEOUT = 120
_VECTOR_STORAGE_NAMES = ('entities_vdb', 'relationships_vdb', 'chunks_vdb')


def _processed(status: dict | None) -> bool:
    return bool(status and str(status.get('status', '')).lower() in {'processed', 'docstatus.processed'})


class KnowledgeGraph:
    def __init__(self, data_dir: Path, database: dict, model_service_factory: Any) -> None:
        require_graph()
        self.directory = data_dir / 'graph'
        self.database = database
        self.model_service_factory = model_service_factory
        self.rag = None
        self._initialization_lock = asyncio.Lock()
        self._llm_slots = asyncio.Semaphore(2)
        self._embedding_slots = asyncio.Semaphore(2)

    async def _ensure(self) -> Any:
        async with self._initialization_lock:
            if self.rag is not None:
                return self.rag
            rag = None
            initialization = None
            try:
                configure_graph_environment()
                tokenizer = await asyncio.to_thread(load_graph_tokenizer)
                rag = self._create_rag(tokenizer)
                initialization = asyncio.create_task(self._initialize_storages(rag))
                await asyncio.wait_for(asyncio.shield(initialization), timeout=_INITIALIZATION_TIMEOUT)
                self.rag = rag
                return rag
            except BaseException as error:
                cancelled_during_cleanup = False
                if initialization is not None:
                    # Own and drain this task explicitly. On Python 3.10 a
                    # second cancellation can interrupt wait_for's own cleanup
                    # wait while its child is still using a storage client.
                    initialization.cancel()
                    cancelled_during_cleanup = await _wait_for_completion(initialization)
                    if not initialization.cancelled():
                        initialization.exception()
                if rag is not None:
                    cleanup = asyncio.create_task(self._finalize(rag, partial=True))
                    cancelled_during_cleanup = await _wait_for_completion(cleanup) or cancelled_during_cleanup
                    if not cleanup.cancelled():
                        cleanup.exception()  # Preserve the original sanitized error.
                if cancelled_during_cleanup:
                    raise asyncio.CancelledError from None
                if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                if isinstance(error, KnowledgeError):
                    raise
                raise KnowledgeError.unavailable() from None

    @staticmethod
    async def _initialize_storages(rag: Any) -> None:
        from lightrag.kg.shared_storage import get_data_init_lock

        # LightRAG 1.5.7's async Milvus initializer runs synchronous connection,
        # schema and collection-load RPCs. Keep its lock on this event loop while
        # only those blocking steps run in a worker. _blocking waits for a
        # cancelled worker before cleanup can close the client it is still using.
        for name in _VECTOR_STORAGE_NAMES:
            storage = getattr(rag, name)
            async with get_data_init_lock():
                if not storage._initialized:
                    await _blocking(lambda storage=storage: KnowledgeGraph._initialize_milvus_storage(storage))
        # The pinned SDK's vector initialize() now only takes its lock and
        # returns. PostgreSQL and pipeline initialization remain on this loop.
        await rag.initialize_storages()

    @staticmethod
    def _initialize_milvus_storage(storage: Any) -> None:
        from lightrag.kg.milvus_impl import INDEX_VERSION_REQUIREMENTS

        if storage._client is None:
            storage._client = storage._create_milvus_client()
        if storage.index_config.index_type in INDEX_VERSION_REQUIREMENTS:
            storage.index_config.validate_milvus_version(storage._client.get_server_version())
        storage._create_collection_if_not_exist()
        storage._initialized = True

    def _create_rag(self, tokenizer: Any) -> Any:
        import numpy as np
        from lightrag import LightRAG
        from lightrag.utils import EmbeddingFunc

        spec = dict(self.database.get('index_spec') or {})
        if not spec or spec['model_id'] != self.database['embed_model_id']:
            raise KnowledgeError.reindex()

        async def embed(texts: Sequence[str], **kwargs) -> Any:
            try:
                async with self._embedding_slots:
                    async with self.model_service_factory() as service:
                        resolved = await asyncio.wait_for(
                            service.resolve_model(spec['model_id'], expected_type='embedding'),
                            timeout=8,
                        )
                    if (
                        resolved.platform_model_id != spec['model_id']
                        or embedding_fingerprint(resolved) != spec['fingerprint']
                    ):
                        raise KnowledgeError.reindex()
                    # A fresh session prevents an identity-map cache from hiding
                    # a binding change between resolution and the remote call.
                    async with self.model_service_factory() as service:
                        result = await asyncio.wait_for(
                            service.embed(spec['model_id'], [normalize_embedding_text(text) for text in texts]),
                            timeout=_EMBEDDING_TIMEOUT,
                        )
                    if any(
                        getattr(result, field, None) != getattr(resolved, field)
                        for field in ('platform_model_id', 'binding_id', 'engine_model_snapshot')
                    ):
                        raise KnowledgeError.reindex()
                vectors = sorted(result.response.vectors, key=lambda item: item.index)
                if [item.index for item in vectors] != list(range(len(texts))):
                    raise KnowledgeError.reindex()
                values = np.array([vector.values for vector in vectors], dtype=np.float32)
                if values.shape != (len(texts), spec['dimension']) or not np.isfinite(values).all():
                    raise KnowledgeError.reindex()
                return values
            except KnowledgeError:
                raise
            except Exception:
                raise KnowledgeError.unavailable() from None

        async def complete(
            prompt: str, system_prompt: str | None = None, history_messages: list[dict] | None = None, **kwargs
        ) -> str:
            try:
                messages = []
                if system_prompt:
                    messages.append(ModelMessage(role='system', content=system_prompt))
                messages.extend(
                    ModelMessage(role=item['role'], content=item['content']) for item in (history_messages or [])
                )
                messages.append(ModelMessage(role='user', content=prompt))
                async with self._llm_slots, self.model_service_factory() as service:
                    result = await asyncio.wait_for(
                        service.chat(self.database['llm_model_id'], messages),
                        timeout=_LLM_TIMEOUT,
                    )
                return result.response.text
            except KnowledgeError:
                raise
            except Exception:
                raise KnowledgeError.unavailable() from None

        return LightRAG(
            working_dir=str(self.directory),
            workspace=self.database['db_id'],
            tokenizer=tokenizer,
            embedding_func=EmbeddingFunc(
                embedding_dim=spec['dimension'],
                max_token_size=4096,
                model_name=self.database['embed_model_id'],
                func=embed,
            ),
            llm_model_func=complete,
            llm_model_name=self.database['llm_model_id'],
            vector_storage='MilvusVectorDBStorage',
            kv_storage='PGKVStorage',
            graph_storage='PGTableGraphStorage',
            doc_status_storage='PGDocStatusStorage',
            chunk_token_size=self.database['chunk_size'],
            chunk_overlap_token_size=self.database['chunk_overlap'],
            embedding_func_max_async=2,
            embedding_batch_num=16,
            llm_model_max_async=2,
            max_parallel_insert=1,
            default_embedding_timeout=_EMBEDDING_TIMEOUT,
            default_llm_timeout=_LLM_TIMEOUT,
            addon_params={'language': 'Chinese'},
        )

    async def insert(self, doc_id: str, content: str, title: str) -> None:
        try:
            rag = await self._ensure()
            if _processed(await rag.doc_status.get_by_id(doc_id)):
                return
            await rag.ainsert(input=content, ids=doc_id, file_paths=title)
            if not _processed(await rag.doc_status.get_by_id(doc_id)):
                raise KnowledgeError('KNOWLEDGE_GRAPH_INDEX_FAILED', '知识图谱索引未完成，请重试', 503)
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError('KNOWLEDGE_GRAPH_INDEX_FAILED', '知识图谱索引未完成，请重试', 503) from None

    async def delete_document(self, doc_id: str) -> None:
        try:
            rag = await self._ensure()
            result = await rag.adelete_by_doc_id(doc_id)
            if getattr(result, 'status', None) not in {'success', 'not_found'}:
                raise KnowledgeError.unavailable()
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError.unavailable() from None

    async def labels(self) -> list[str]:
        try:
            return await (await self._ensure()).get_graph_labels()
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError.unavailable() from None

    async def subgraph(self, node_label: str, max_depth: int, max_nodes: int) -> dict:
        try:
            graph = await (await self._ensure()).get_knowledge_graph(
                node_label=node_label,
                max_depth=max_depth,
                max_nodes=max_nodes,
            )
            nodes = [
                {
                    'id': node.id,
                    'entity_name': node.properties.get('entity_id') or node.properties.get('name') or node.id,
                    'entity_type': node.properties.get('entity_type', 'unknown'),
                }
                for node in graph.nodes
            ]
            edges = [
                {'id': edge.id, 'source': edge.source, 'target': edge.target, 'type': edge.type} for edge in graph.edges
            ]
            return {'nodes': nodes, 'edges': edges, 'is_truncated': graph.is_truncated}
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError.unavailable() from None

    async def query(self, query: str, params: QueryParameters) -> str:
        try:
            from lightrag import QueryParam

            return await (await self._ensure()).aquery(
                query,
                QueryParam(mode=params.mode, only_need_context=params.only_need_context, top_k=params.top_k),
            )
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError.unavailable() from None

    async def delete_database(self, document_ids: Sequence[str]) -> None:
        try:
            rag = await self._ensure()
            vector_collections = []
            for name in _VECTOR_STORAGE_NAMES:
                storage = getattr(rag, name)
                if storage.workspace != self.database['db_id'] or not storage.final_namespace.startswith(
                    f'{self.database["db_id"]}_'
                ):
                    raise KnowledgeError.unavailable()
                # Pin the SDK's exact names and connection settings before its
                # clients are closed; never enumerate/drop other collections.
                vector_collections.append((storage.final_namespace, storage._get_milvus_connection_kwargs()))
            await self._stop_model_queues(rag, strict=True)
            for name in _VECTOR_STORAGE_NAMES:
                await getattr(rag, name).drop_pending_index_ops()
            # Remove caches and all graph state for this workspace, including
            # partial documents which never reached the platform manifest. Whole
            # workspace deletion needs no per-document LLM entity rebuilding;
            # each drop remains safe to repeat after a partially failed cleanup.
            for name in _STORAGE_NAMES:
                if name in _VECTOR_STORAGE_NAMES:
                    continue
                result = await getattr(rag, name).drop()
                if result.get('status') != 'success':
                    raise KnowledgeError.unavailable()
            await self.close()
            # LightRAG 1.5.7's vector drop() recreates and loads an empty
            # collection. Permanent KB deletion instead uses the public SDK
            # after every buffered writer and client has been stopped.
            await _blocking(lambda: self._drop_vector_collections(vector_collections))
            path = self.directory / self.database['db_id']
            if path.exists():
                await asyncio.to_thread(shutil.rmtree, path)
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError.unavailable() from None

    @staticmethod
    def _drop_vector_collections(collections: Sequence[tuple[str, dict]]) -> None:
        from pymilvus import MilvusClient

        for name, connection in collections:
            client = MilvusClient(**connection, timeout=MILVUS_REQUEST_TIMEOUT_SECONDS)
            try:
                if client.has_collection(name, timeout=MILVUS_REQUEST_TIMEOUT_SECONDS):
                    client.drop_collection(name, timeout=MILVUS_REQUEST_TIMEOUT_SECONDS)
            finally:
                client.close()

    @staticmethod
    async def _stop_model_queues(rag: Any, *, strict: bool = False) -> None:
        # Stop model queues before releasing storage: cancelled indexing work
        # must not continue writing through already closed clients.
        funcs = [getattr(getattr(rag, 'embedding_func', None), 'func', None)]
        funcs.extend(state.wrapped for state in getattr(rag, '_role_llm_states', {}).values())
        failed = False
        for func in funcs:
            shutdown = getattr(func, 'shutdown', None)
            if shutdown is not None:
                try:
                    await shutdown(graceful=False)
                except Exception:
                    failed = True
        if failed and strict:
            raise KnowledgeError.unavailable()

    @classmethod
    async def _finalize(cls, rag: Any, *, partial: bool = False) -> None:
        await cls._stop_model_queues(rag)
        # In 1.5.7 finalize_storages skips all storage cleanup when initialization
        # failed before reaching INITIALIZED. Release every acquired client then.
        if partial:
            for name in _STORAGE_NAMES:
                storage = getattr(rag, name, None)
                if storage is not None:
                    try:
                        await storage.finalize()
                    except Exception:
                        pass
        await rag.finalize_storages()

    async def close(self) -> None:
        async with self._initialization_lock:
            if self.rag is not None:
                rag, self.rag = self.rag, None
                try:
                    await self._finalize(rag)
                except Exception:
                    raise KnowledgeError.unavailable() from None
