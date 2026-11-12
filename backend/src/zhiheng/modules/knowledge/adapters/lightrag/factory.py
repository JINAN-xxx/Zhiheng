"""Pinned SDK construction, operation dispatch and complete storage finalization."""

import asyncio
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from lightrag import LightRAG, QueryParam  # type: ignore[import-untyped]
from lightrag.utils import EmbeddingFunc, Tokenizer  # type: ignore[import-untyped]
from zhiheng.modules.knowledge.adapters.lightrag.managed import insert_managed
from zhiheng.modules.knowledge.adapters.lightrag.results import query_result, subgraph_result
from zhiheng.modules.knowledge.adapters.lightrag.tokenizer import OfficialBPETokenizer
from zhiheng.modules.knowledge.graph.contracts import GraphFrozen, GraphVectorManifest
from zhiheng.shared.errors import AppError

LABEL_LIMIT = 200


def create_rag(
    *, root: Path, frozen: GraphFrozen, manifest: GraphVectorManifest, llm: Any, embedding: Any, rerank: Any = None
) -> Any:
    return LightRAG(
        working_dir=str(root / manifest.attempt_id.hex),
        workspace=manifest.workspace,
        tokenizer=Tokenizer(model_name='gpt-4o-mini', tokenizer=OfficialBPETokenizer()),
        kv_storage='GraphKVStorage',
        doc_status_storage='GraphDocStorage',
        graph_storage='GraphTableStorage',
        vector_storage='GraphVectorStorage',
        embedding_func=EmbeddingFunc(
            embedding_dim=manifest.dimension, max_token_size=8192, func=embedding, model_name='managed'
        ),
        llm_model_func=llm,
        rerank_model_func=rerank,
        min_rerank_score=0.0,
        role_llm_configs={
            role: {
                'func': partial(llm, _role=role),
                'max_async': frozen.config.concurrency,
                'timeout': frozen.config.timeout_seconds,
                'kwargs': {},
            }
            for role in ('extract', 'keyword', 'query', 'vlm')
        },
        llm_model_name='managed',
        llm_model_max_async=frozen.config.concurrency,
        embedding_func_max_async=frozen.config.concurrency,
        max_parallel_insert=frozen.config.concurrency,
        entity_extract_max_gleaning=1,
        force_llm_summary_on_merge=3,
        default_llm_timeout=frozen.config.timeout_seconds,
        default_embedding_timeout=frozen.config.timeout_seconds,
        summary_max_tokens=min(frozen.config.max_output_tokens, 2048),
        summary_context_size=12000,
        max_total_tokens=12000,
        enable_llm_cache=False,
        enable_llm_cache_for_entity_extract=False,
        vector_db_storage_cls_kwargs={
            'cosine_better_than_threshold': 0.0,
            'index_type': 'HNSW',
            'metric_type': 'COSINE',
        },
        addon_params={'language': 'Chinese'},
    )


async def run_operation(
    rag: Any,
    operation: str,
    frozen: GraphFrozen,
    manifest: GraphVectorManifest,
    request: dict[str, object],
    *,
    rerank_state: dict[str, Any] | None = None,
) -> dict[str, object]:
    result: dict[str, object]
    if operation in {'build', 'preview'}:
        selected = list(frozen.chunks)
        requested = request.get('document_id')
        if requested:
            selected = [chunk for chunk in selected if str(chunk.document_id) == requested]
        if operation == 'preview':
            selected = selected[:3]
        await insert_managed(rag, selected, manifest.attempt_id)
        grouped = {chunk.document_id for chunk in selected}
        counts = await rag.doc_status.get_status_counts()
        if counts.get('processed', 0) != len(grouped) or any(
            value for key, value in counts.items() if key != 'processed'
        ):
            raise AppError('graph_build_incomplete', '图谱抽取未完整成功', 502)
        result = {
            'entities': len(await rag.chunk_entity_relation_graph.get_all_nodes()),
            'relations': len(await rag.chunk_entity_relation_graph.get_all_edges()),
            'chunks': len(selected),
            'seconds': 0.0,
        }
    elif operation == 'query':
        if request.get('_rerank_binding') is None:
            raise AppError('knowledge_rerank_required', '图谱检索必须配置重排模型', 409)
        response = await rag.aquery_data(
            str(request['query']),
            param=QueryParam(
                mode=request.get('mode', 'mix'),
                top_k=40,
                chunk_top_k=request.get('top_k', 8),
                only_need_context=True,
                enable_rerank=True,
            ),
        )
        state = rerank_state or {}
        diagnostics = {key: value for key, value in state.items() if key != 'selected_contents'}
        diagnostics.setdefault('candidate_count', 0)
        diagnostics['retrieval_modes'] = {str(frozen.base_id): 'graph'}
        diagnostics.setdefault('rerank_status', 'no_candidates')
        result = query_result(
            response,
            frozen,
            selected_contents=state.get('selected_contents'),
            diagnostics=diagnostics,
        ).model_dump(mode='json')
    elif operation == 'labels':
        labels = await rag.chunk_entity_relation_graph.get_popular_labels(limit=LABEL_LIMIT + 1)
        result = {'items': labels[:LABEL_LIMIT], 'truncated': len(labels) > LABEL_LIMIT}
    else:
        graph = await rag.chunk_entity_relation_graph.get_knowledge_graph(
            node_label=request.get('node_label', '*'),
            max_depth=request.get('max_depth', 2),
            max_nodes=request.get('max_nodes', 100),
        )
        result = subgraph_result(graph, manifest.revision).model_dump(mode='json')
    return result


async def shutdown_rag(
    rag: Any,
    owned_tasks: set[asyncio.Task[Any]],
    *,
    on_error: Callable[[], None] | None = None,
) -> None:
    errors: list[BaseException] = []

    def failed(error: BaseException) -> None:
        errors.append(error)
        if on_error is not None:
            on_error()

    if rag is not None:
        for cb in [*rag.role_llm_funcs.values(), getattr(rag, 'rerank_model_func', None)]:
            if hasattr(cb, 'shutdown'):
                try:
                    await cb.shutdown(graceful=False, timeout=5)
                except BaseException as error:
                    failed(error)
        if hasattr(rag.embedding_func.func, 'shutdown'):
            try:
                await rag.embedding_func.func.shutdown(graceful=False, timeout=5)
            except BaseException as error:
                failed(error)
    # SDK queue workers deliberately catch cancellation while the queue is
    # running. Set their shutdown flag through the real SDK lifecycle first;
    # otherwise cancelling then gathering them waits forever on queue.get().
    current = asyncio.current_task()
    pending = [task for task in owned_tasks if task is not current and not task.done()]
    for task in pending:
        task.cancel()
    if rag is not None:
        for name in (
            'full_docs',
            'text_chunks',
            'full_entities',
            'full_relations',
            'entity_chunks',
            'relation_chunks',
            'llm_response_cache',
            'doc_status',
            'chunk_entity_relation_graph',
            'entities_vdb',
            'relationships_vdb',
            'chunks_vdb',
        ):
            storage = getattr(rag, name, None)
            if storage is None:
                continue
            try:
                await storage.finalize()
            except BaseException as exc:
                failed(exc)
    # Do not abandon later storage/RPC close obligations merely because a
    # failed extension keeps one task alive. Its actual drain still owns this
    # coroutine and cannot grant stopped/GC authority before it terminates.
    await asyncio.gather(*pending, return_exceptions=True)
    if errors:
        raise errors[0]
