import asyncio
import hashlib
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from zhiheng.modules.knowledge.adapters.lightrag.results import query_result
from zhiheng.modules.knowledge.control import VectorIOControl
from zhiheng.modules.knowledge.graph.contracts import GraphChunk, sdk_chunk_id
from zhiheng.modules.models.public import ModelBinding
from zhiheng.shared.errors import AppError


def chunk(text: str) -> GraphChunk:
    document, identity = uuid4(), uuid4()
    return GraphChunk(
        id=identity,
        document_id=document,
        file_id=uuid4(),
        index_revision=uuid4(),
        document_revision=uuid4(),
        ordinal=0,
        content=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        sdk_id=sdk_chunk_id(document, identity),
    )


def test_graph_sources_keep_retrieval_order_and_validate_sdk_text() -> None:
    first, second = chunk('first'), chunk('second')
    frozen = SimpleNamespace(chunks=[first, second])
    raw = {
        'status': 'success',
        'data': {
            'chunks': [{'chunk_id': second.sdk_id, 'content': 'second'}, {'chunk_id': first.sdk_id, 'content': 'first'}]
        },
    }
    result = query_result(raw, frozen)
    assert [source.chunk_id for source in result.sources] == [second.id, first.id]
    raw['data']['chunks'][0]['content'] = 'forged'
    with pytest.raises(AppError):
        query_result(raw, frozen)


async def test_graph_ranker_is_real_controlled_callback_and_rejects_unknown_text() -> None:
    from zhiheng.modules.knowledge.adapters.lightrag.sdk import _rerank_callback

    calls = []
    first, second = chunk('first'), chunk('second')
    frozen = SimpleNamespace(chunks=[first, second])
    diagnostics, failures = {}, []

    async def model(call: Any) -> dict[str, Any]:
        calls.append(call)
        return {'results': [{'index': 1, 'relevance_score': 0.9}, {'index': 0, 'relevance_score': 0.2}]}

    callback = _rerank_callback(
        model, asyncio.get_running_loop(), VectorIOControl(timeout_seconds=10), frozen, diagnostics, failures
    )
    result = await asyncio.to_thread(
        lambda: asyncio.run(callback(query='query', documents=['first', 'second'], top_n=8))
    )
    assert calls[0].role == 'rerank' and calls[0].operation == 'rerank'
    assert [message.content for message in calls[0].messages] == ['query', 'first', 'second']
    assert [row['index'] for row in result] == [1, 0]
    assert diagnostics['rerank_status'] == 'completed' and not failures
    with pytest.raises(AppError):
        await asyncio.to_thread(lambda: asyncio.run(callback(query='query', documents=['not authorized'], top_n=8)))
    assert len(calls) == 1
    assert failures, 'SDK swallows Exception; fatal callback errors must be retained and rethrown before output'


async def test_graph_diagnostics_share_complete_shape_even_without_candidates() -> None:
    from zhiheng.modules.knowledge.adapters.lightrag.factory import run_operation

    base = uuid4()
    frozen = SimpleNamespace(chunks=[], base_id=base)

    class RAG:
        async def aquery_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            assert kwargs['param'].enable_rerank is True
            return {'status': 'success', 'data': {'chunks': []}}

    binding = ModelBinding(
        model_id=uuid4(), connection_id=uuid4(), upstream_model='ranker', max_output_tokens=64, model_type='rerank'
    )
    result = await run_operation(
        RAG(), 'query', frozen, None, {'query': 'no results', '_rerank_binding': binding.model_dump(mode='json')}
    )
    assert result['diagnostics']['candidate_count'] == 0
    assert result['diagnostics']['retrieval_modes'] == {str(base): 'graph'}
    assert result['diagnostics']['rerank_status'] == 'no_candidates'


async def test_graph_query_without_ranker_fails_before_sdk_recall() -> None:
    from zhiheng.modules.knowledge.adapters.lightrag.factory import run_operation

    calls = []

    class RAG:
        async def aquery_data(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(args)
            return {}

    with pytest.raises(AppError) as error:
        await run_operation(
            RAG(), 'query', SimpleNamespace(chunks=[], base_id=uuid4()), None, {'query': 'missing ranker'}
        )
    assert error.value.code == 'knowledge_rerank_required' and not calls


async def test_graph_transient_ranker_failure_is_retained_as_fatal() -> None:
    from zhiheng.modules.knowledge.adapters.lightrag.sdk import _rerank_callback

    diagnostics, failures = {}, []

    async def model(call: Any) -> dict[str, Any]:
        return {'_rerank_fallback': 'knowledge_rerank_transient'}

    callback = _rerank_callback(
        model,
        asyncio.get_running_loop(),
        VectorIOControl(timeout_seconds=10),
        SimpleNamespace(chunks=[chunk('candidate')]),
        diagnostics,
        failures,
    )
    with pytest.raises(AppError) as error:
        await asyncio.to_thread(lambda: asyncio.run(callback(query='question', documents=['candidate'])))
    assert error.value.code == 'knowledge_rerank_failed'
    assert failures and diagnostics.get('rerank_status') != 'fallback'
