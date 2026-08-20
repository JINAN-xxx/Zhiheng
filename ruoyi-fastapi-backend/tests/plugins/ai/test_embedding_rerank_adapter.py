from collections.abc import AsyncIterator, Mapping
from decimal import Decimal

import pytest

from plugins.ai.domain.model_runtime import (
    ModelEmbeddingRequest,
    ModelEmbeddingVector,
    ModelRerankDocument,
    ModelRerankRequest,
)
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.infrastructure.litellm_client import RuntimeRerankResult
from plugins.ai.infrastructure.litellm_runtime_adapter import LiteLLMRuntimeAdapter

EMBEDDING_REQUEST = ModelEmbeddingRequest(
    engine_model='runtime-embedding-v1',
    inputs=('first document', 'second document'),
    dimensions=2,
    user='external-user-42',
)
RERANK_REQUEST = ModelRerankRequest(
    engine_model='runtime-rerank-v1',
    query='Which document describes the refund policy?',
    documents=(
        ModelRerankDocument(text='Shipping usually takes three days.'),
        ModelRerankDocument(text='Refunds are accepted within thirty days.'),
    ),
    top_n=2,
    return_documents=True,
)


class FakeRuntimeClient:
    def __init__(
        self,
        *,
        embedding_response: Mapping[str, object] | None = None,
        rerank_result: RuntimeRerankResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.embedding_response = embedding_response
        self.rerank_result = rerank_result
        self.error = error
        self.embedding_payloads: list[Mapping[str, object]] = []
        self.rerank_payloads: list[Mapping[str, object]] = []
        self.embedding_limits: list[int | None] = []
        self.rerank_limits: list[int | None] = []

    async def list_models(self) -> Mapping[str, object]:
        return {'data': []}

    async def create_chat_completion(self, payload: Mapping[str, object]) -> object:
        del payload
        raise AssertionError('unused')

    async def create_embedding(
        self,
        payload: Mapping[str, object],
        *,
        max_json_response_bytes: int | None = None,
    ) -> Mapping[str, object]:
        self.embedding_payloads.append(payload)
        self.embedding_limits.append(max_json_response_bytes)
        if self.error is not None:
            raise self.error
        assert self.embedding_response is not None
        return self.embedding_response

    async def create_rerank(
        self,
        payload: Mapping[str, object],
        *,
        max_json_response_bytes: int | None = None,
    ) -> RuntimeRerankResult:
        self.rerank_payloads.append(payload)
        self.rerank_limits.append(max_json_response_bytes)
        if self.error is not None:
            raise self.error
        assert self.rerank_result is not None
        return self.rerank_result

    def stream_chat_completion(self, payload: Mapping[str, object]) -> AsyncIterator[bytes]:
        del payload
        raise AssertionError('unused')

    async def aclose(self) -> None:
        return None


def _adapter(client: FakeRuntimeClient) -> LiteLLMRuntimeAdapter:
    return LiteLLMRuntimeAdapter(client, max_json_response_bytes=33_554_432)


def _embedding_response() -> Mapping[str, object]:
    return {
        'object': 'list',
        'data': [
            {'object': 'embedding', 'index': 0, 'embedding': [0.0123, -0.0456]},
            {'object': 'embedding', 'index': 1, 'embedding': [0.5, 0.25]},
        ],
        'model': 'PRIVATE-runtime-alias',
        'usage': {'prompt_tokens': 8, 'total_tokens': 8},
    }


def _rerank_result() -> RuntimeRerankResult:
    return RuntimeRerankResult(
        payload={
            'id': 'PRIVATE-rerank-id',
            'results': [
                {'index': 1, 'relevance_score': 0.9921},
                {'index': 0, 'relevance_score': 0.1100},
            ],
            'meta': {
                'billed_units': {'search_units': 1},
                'tokens': {'input_tokens': 22},
            },
        },
        response_cost='0.001',
        request_id='PRIVATE-request-id',
    )


@pytest.mark.asyncio
async def test_embed_builds_strict_payload_and_normalizes_finite_vectors() -> None:
    client = FakeRuntimeClient(embedding_response=_embedding_response())
    adapter = _adapter(client)

    response = await adapter.embed(EMBEDDING_REQUEST)

    assert client.embedding_payloads == [
        {
            'model': 'runtime-embedding-v1',
            'input': ['first document', 'second document'],
            'dimensions': 2,
            'user': 'external-user-42',
        }
    ]
    assert client.embedding_limits == [33_554_432]
    assert response.input_tokens == 8
    assert response.raw_cost is None
    assert response.vectors == (
        ModelEmbeddingVector(index=0, values=(0.0123, -0.0456)),
        ModelEmbeddingVector(index=1, values=(0.5, 0.25)),
    )


@pytest.mark.asyncio
async def test_embed_accepts_missing_usage_with_null_input_tokens() -> None:
    payload = _embedding_response()
    del payload['usage']
    client = FakeRuntimeClient(embedding_response=payload)
    adapter = _adapter(client)

    response = await adapter.embed(EMBEDDING_REQUEST)

    assert response.input_tokens is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'mutator',
    (
        lambda payload: payload['data'].pop(),
        lambda payload: payload['data'].__setitem__(0, {'index': 0, 'embedding': [0.1, float('nan')]}),
        lambda payload: payload['data'].__setitem__(1, {'index': 1, 'embedding': ['0.5', 0.25]}),
        lambda payload: payload['data'].__setitem__(1, {'index': 2, 'embedding': [0.5, 0.25]}),
        lambda payload: payload['data'].__setitem__(0, {'index': 0, 'embedding': [0.1, 0.2, 0.3]}),
        lambda payload: payload['data'].__setitem__(0, {'index': 0, 'embedding': [0.1, 0.2, 0.3, 0.4]}),
    ),
)
async def test_embed_rejects_malformed_response(mutator: object) -> None:
    payload = _embedding_response()
    mutator(payload)  # type: ignore[operator]
    client = FakeRuntimeClient(embedding_response=payload)
    adapter = _adapter(client)

    with pytest.raises(ModelServiceError) as raised:
        await adapter.embed(EMBEDDING_REQUEST)

    assert raised.value.code == 'MODEL_RUNTIME_ERROR'


@pytest.mark.asyncio
async def test_embed_rejects_dimension_mismatch_with_requested_dimensions() -> None:
    payload = _embedding_response()
    payload['data'][0]['embedding'] = [0.1, 0.2, 0.3]
    client = FakeRuntimeClient(embedding_response=payload)
    adapter = _adapter(client)

    with pytest.raises(ModelServiceError):
        await adapter.embed(EMBEDDING_REQUEST)


@pytest.mark.asyncio
async def test_rerank_builds_strict_payload_sorts_stably_and_normalizes_meta() -> None:
    client = FakeRuntimeClient(rerank_result=_rerank_result())
    adapter = _adapter(client)

    response = await adapter.rerank(RERANK_REQUEST)

    assert client.rerank_payloads == [
        {
            'model': 'runtime-rerank-v1',
            'query': 'Which document describes the refund policy?',
            'documents': [
                {'text': 'Shipping usually takes three days.'},
                {'text': 'Refunds are accepted within thirty days.'},
            ],
            'top_n': 2,
            'return_documents': True,
        }
    ]
    assert client.rerank_limits == [33_554_432]
    assert response.request_id == 'PRIVATE-request-id'
    assert [(item.index, item.relevance_score) for item in response.results] == [(1, 0.9921), (0, 0.11)]
    assert response.input_tokens == 22
    assert response.search_units == 1
    assert response.raw_cost == Decimal('0.001')


@pytest.mark.asyncio
async def test_rerank_generates_platform_request_id_when_upstream_id_missing() -> None:
    result = _rerank_result()
    result.payload.pop('id', None)
    client = FakeRuntimeClient(rerank_result=RuntimeRerankResult(payload=result.payload))
    adapter = _adapter(client)

    response = await adapter.rerank(RERANK_REQUEST)

    assert response.request_id.startswith('rrk_')
    assert len(response.request_id) == 36


@pytest.mark.asyncio
async def test_rerank_stable_sort_ties_break_by_original_index() -> None:
    result = RuntimeRerankResult(
        payload={
            'results': [
                {'index': 3, 'relevance_score': 0.5},
                {'index': 1, 'relevance_score': 0.9},
                {'index': 0, 'relevance_score': 0.9},
            ],
            'meta': {'billed_units': {'search_units': 1}, 'tokens': {'input_tokens': 10}},
        }
    )
    request = ModelRerankRequest(
        engine_model='runtime-rerank-v1',
        query='q',
        documents=tuple(ModelRerankDocument(text=f'd{i}') for i in range(4)),
        top_n=None,
    )
    client = FakeRuntimeClient(rerank_result=result)
    adapter = _adapter(client)

    response = await adapter.rerank(request)

    assert [item.index for item in response.results] == [0, 1, 3]


@pytest.mark.asyncio
async def test_rerank_respects_top_n_and_missing_search_units() -> None:
    result = _rerank_result()
    result.payload['meta'] = {'tokens': {'input_tokens': 22}}
    client = FakeRuntimeClient(rerank_result=result)
    adapter = _adapter(client)
    request = ModelRerankRequest(
        engine_model='runtime-rerank-v1',
        query='q',
        documents=RERANK_REQUEST.documents,
        top_n=1,
    )

    response = await adapter.rerank(request)

    assert len(response.results) == 1
    assert response.search_units is None
    assert response.input_tokens == 22


@pytest.mark.asyncio
async def test_rerank_rejects_index_out_of_document_range_or_duplicated() -> None:
    for results in (
        [{'index': 2, 'relevance_score': 0.9}],
        [{'index': 0, 'relevance_score': 0.9}, {'index': 0, 'relevance_score': 0.8}],
    ):
        result = RuntimeRerankResult(payload={'results': results})
        client = FakeRuntimeClient(rerank_result=result)
        adapter = _adapter(client)

        with pytest.raises(ModelServiceError):
            await adapter.rerank(RERANK_REQUEST)


@pytest.mark.asyncio
async def test_embedding_and_rerank_requests_validate_input_bounds() -> None:
    with pytest.raises(ValueError):
        ModelEmbeddingRequest(engine_model='m', inputs=())
    with pytest.raises(ValueError):
        ModelEmbeddingRequest(engine_model='m', inputs=('x' * 32769,))
    with pytest.raises(ValueError):
        ModelEmbeddingRequest(engine_model='m', inputs=('ok',), dimensions=0)
    with pytest.raises(ValueError):
        ModelRerankRequest(engine_model='m', query='q', documents=())
    with pytest.raises(ValueError):
        ModelRerankRequest(engine_model='m', query='q', documents=(ModelRerankDocument(text='d'),), top_n=2)
    with pytest.raises(ValueError):
        ModelRerankRequest(engine_model='m', query='x' * 8193, documents=(ModelRerankDocument(text='d'),))
