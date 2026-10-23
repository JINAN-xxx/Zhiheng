import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from plugins.ai.domain.model_runtime import (
    ModelChatResponse,
    ModelEmbeddingResponse,
    ModelEmbeddingVector,
    ModelMessage,
    ModelRerankResponse,
    ModelRerankResult,
    ModelStreamEvent,
    ModelUsage,
)
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.entity.vo.ai_openai_compat_vo import (
    OpenAIChatCompletionRequestModel,
    OpenAIEmbeddingRequestModel,
    OpenAIRerankRequestModel,
)
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity
from plugins.ai.infrastructure.openai_stream_adapter import OpenAIStreamAdapter
from plugins.ai.service.model_service import ModelServiceChatResult, ModelServiceStreamResult
from plugins.ai.service.openai_compat_service import OpenAICompatService
from plugins.ai.service.public_invocation_coordinator import (
    InvocationResult,
    InvocationSnapshotRecord,
    RuntimeUsage,
    SettledResult,
)

APPLICATION_ID = 'app_00000000000040008000000000000001'
KEY_ID = 'key_00000000000040008000000000000001'
MODEL_ID = 'mdl_00000000000040008000000000000001'
BINDING_ID = 'bnd_00000000000040008000000000000001'
MODEL_CODE = 'deepseek-chat'
ENGINE_MODEL = 'runtime-deepseek-chat'
COMPLETION_ID = 'chatcmpl_00000000000040008000000000000001'
CREATED_AT = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
CREATED = int(CREATED_AT.timestamp())

SNAPSHOT = InvocationSnapshotRecord(
    application_id=APPLICATION_ID,
    api_key_id=KEY_ID,
    platform_model_id=MODEL_ID,
    model_code=MODEL_CODE,
    model_type='chat',
    binding_id=BINDING_ID,
    engine_type='litellm',
    engine_model_snapshot=ENGINE_MODEL,
    max_output_tokens=1024,
    rate_card_id='rate_00000000000040008000000000000001',
    rate_card_version=1,
    billing_mode='token',
    input_price_per_token=Decimal('0.000001'),
    output_price_per_token=Decimal('0.000002'),
    cache_price_per_token=None,
    search_price_per_unit=None,
    currency='USD',
    rpm_limit=None,
    tpm_limit=None,
    concurrency_limit=None,
    monthly_budget=None,
)


class EmptyStream:
    def __aiter__(self) -> 'EmptyStream':
        return self

    async def __anext__(self) -> ModelStreamEvent:
        raise StopAsyncIteration

    async def aclose(self) -> None:
        return None


class FakeCoordinator:
    def __init__(self) -> None:
        self.resolve_calls: list[tuple[DeveloperApiIdentity, str, str]] = []
        self.run_calls: list[tuple[DeveloperApiIdentity, str, str, str]] = []
        self.error: BaseException | None = None
        self.usage: RuntimeUsage | None = RuntimeUsage(input_tokens=3, output_tokens=2)

    async def resolve_invocation(
        self,
        identity: DeveloperApiIdentity,
        model_code: str,
        expected_type: str,
    ) -> InvocationSnapshotRecord:
        self.resolve_calls.append((identity, model_code, expected_type))
        if self.error is not None:
            raise self.error
        return SNAPSHOT

    async def run(
        self,
        identity: DeveloperApiIdentity,
        model_code: str,
        expected_type: str,
        *,
        operation_type: str,
        input_bytes: int,
        output_ceiling: int | None = None,
        operation: object = None,
    ) -> InvocationResult:
        self.run_calls.append((identity, model_code, expected_type, operation_type))
        snapshot = await self.resolve_invocation(identity, model_code, expected_type)
        result = await operation(snapshot)  # type: ignore[operator]
        settled = SettledResult(
            request_id='use_00000000000040008000000000000001',
            status='completed',
            input_tokens=self.usage.input_tokens if self.usage is not None else None,
            output_tokens=self.usage.output_tokens if self.usage is not None else None,
            cache_tokens=None,
            reasoning_tokens=None,
            search_units=None,
            billable_cost=Decimal('0.000007'),
            cost_estimated=False,
            error_code=None,
        )
        return InvocationResult(snapshot=snapshot, settled=settled, payload=result.value)

    async def run_stream(
        self,
        identity: DeveloperApiIdentity,
        model_code: str,
        expected_type: str,
        *,
        operation_type: str,
        input_bytes: int,
        output_ceiling: int | None = None,
        operation: object = None,
    ) -> object:
        self.run_calls.append((identity, model_code, expected_type, operation_type))
        snapshot = await self.resolve_invocation(identity, model_code, expected_type)
        return await operation(snapshot)  # type: ignore[operator]


class FakeModelService:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[str, Sequence[ModelMessage], Mapping[str, object]]] = []
        self.stream_calls: list[tuple[str, Sequence[ModelMessage], Mapping[str, object]]] = []
        self.embed_calls: list[tuple[str, tuple[str, ...], Mapping[str, object]]] = []
        self.rerank_calls: list[tuple[str, str, tuple[str, ...], Mapping[str, object]]] = []
        self.error: BaseException | None = None
        self.stream = ModelServiceStreamResult(
            platform_model_id=MODEL_ID,
            binding_id=BINDING_ID,
            engine_type='litellm',
            engine_model_snapshot=ENGINE_MODEL,
            _stream=EmptyStream(),
        )

    async def chat(
        self,
        model_id: str,
        messages: Sequence[ModelMessage],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceChatResult:
        self.chat_calls.append((model_id, messages, {} if parameters is None else parameters))
        if self.error is not None:
            raise self.error
        return ModelServiceChatResult(
            platform_model_id=MODEL_ID,
            binding_id=BINDING_ID,
            engine_type='litellm',
            engine_model_snapshot=ENGINE_MODEL,
            response=ModelChatResponse(
                text='Hello',
                reasoning='PRIVATE reasoning',
                usage=ModelUsage(input_tokens=3, output_tokens=2, reasoning_tokens=1),
                request_id='PRIVATE-runtime-request-id',
            ),
        )

    async def stream_chat(
        self,
        model_id: str,
        messages: Sequence[ModelMessage],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceStreamResult:
        self.stream_calls.append((model_id, messages, {} if parameters is None else parameters))
        if self.error is not None:
            raise self.error
        return self.stream

    async def embed(
        self,
        model_id: str,
        inputs: Sequence[str],
        parameters: Mapping[str, object] | None = None,
    ) -> object:
        self.embed_calls.append((model_id, tuple(inputs), {} if parameters is None else parameters))
        if self.error is not None:
            raise self.error
        return ModelServiceEmbeddingResultLike(
            ModelEmbeddingResponse(
                vectors=(ModelEmbeddingVector(index=0, values=(0.1, 0.2)),),
                input_tokens=4,
            )
        )

    async def rerank(
        self,
        model_id: str,
        query: str,
        documents: Sequence[str],
        parameters: Mapping[str, object] | None = None,
    ) -> object:
        self.rerank_calls.append((model_id, query, tuple(documents), {} if parameters is None else parameters))
        if self.error is not None:
            raise self.error
        return ModelServiceRerankResultLike(
            ModelRerankResponse(
                request_id='rrk_PRIVATE',
                results=(ModelRerankResult(index=0, relevance_score=0.9),),
                input_tokens=5,
                search_units=1,
            )
        )


class ModelServiceEmbeddingResultLike:
    def __init__(self, response: object) -> None:
        self.response = response


class ModelServiceRerankResultLike:
    def __init__(self, response: object) -> None:
        self.response = response


def identity() -> DeveloperApiIdentity:
    return DeveloperApiIdentity(application_id=APPLICATION_ID, key_id=KEY_ID)


def request(*, stream: bool = False) -> OpenAIChatCompletionRequestModel:
    return OpenAIChatCompletionRequestModel.model_validate(
        {
            'model': MODEL_CODE,
            'messages': [
                {'role': 'system', 'content': 'Be concise.'},
                {'role': 'user', 'content': 'Hello'},
            ],
            'stream': stream,
            'temperature': 0.2,
            'top_p': 0.9,
            'max_tokens': 128,
            'presence_penalty': 0.1,
            'frequency_penalty': -0.1,
            'stop': ['END'],
            'user': 'caller-42',
        }
    )


def embed_request() -> OpenAIEmbeddingRequestModel:
    return OpenAIEmbeddingRequestModel.model_validate(
        {
            'model': MODEL_CODE,
            'input': ['first document', 'second document'],
            'encoding_format': 'float',
            'dimensions': 1024,
            'user': 'external-user-42',
        }
    )


def rerank_request() -> OpenAIRerankRequestModel:
    return OpenAIRerankRequestModel.model_validate(
        {
            'model': MODEL_CODE,
            'query': 'Which document describes the refund policy?',
            'documents': [
                'Shipping usually takes three days.',
                {'text': 'Refunds are accepted within thirty days.'},
            ],
            'top_n': 2,
            'return_documents': True,
        }
    )


def service() -> tuple[OpenAICompatService, FakeCoordinator, FakeModelService]:
    coordinator = FakeCoordinator()
    model_service = FakeModelService()
    return (
        OpenAICompatService(
            coordinator,
            model_service,
            completion_id_factory=lambda: COMPLETION_ID,
            clock=lambda: CREATED_AT,
        ),
        coordinator,
        model_service,
    )


@pytest.mark.asyncio
async def test_complete_resolves_public_code_calls_platform_model_and_returns_public_shape() -> None:
    compat, coordinator, model_service = service()

    response = await compat.complete(identity(), request())

    assert coordinator.resolve_calls == [(identity(), MODEL_CODE, 'chat')]
    assert len(model_service.chat_calls) == 1
    model_id, messages, parameters = model_service.chat_calls[0]
    assert model_id == MODEL_ID
    assert messages == (
        ModelMessage(role='system', content='Be concise.'),
        ModelMessage(role='user', content='Hello'),
    )
    assert parameters == {
        'temperature': 0.2,
        'top_p': 0.9,
        'max_tokens': 128,
        'presence_penalty': 0.1,
        'frequency_penalty': -0.1,
        'stop': ['END'],
        'user': 'caller-42',
    }
    assert response.model_dump() == {
        'id': COMPLETION_ID,
        'object': 'chat.completion',
        'created': CREATED,
        'model': MODEL_CODE,
        'choices': [
            {
                'index': 0,
                'message': {'role': 'assistant', 'content': 'Hello'},
                'finish_reason': 'stop',
            }
        ],
        'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5},
    }
    serialized = repr(response.model_dump())
    assert MODEL_ID not in serialized
    assert BINDING_ID not in serialized
    assert ENGINE_MODEL not in serialized
    assert 'PRIVATE' not in serialized


@pytest.mark.asyncio
async def test_stream_resolves_before_runtime_and_returns_public_stream_adapter() -> None:
    compat, coordinator, model_service = service()

    result = await compat.stream(identity(), request(stream=True))

    assert type(result) is OpenAIStreamAdapter
    assert coordinator.resolve_calls == [(identity(), MODEL_CODE, 'chat')]
    assert model_service.stream_calls[0][0] == MODEL_ID
    assert model_service.stream_calls[0][1] == (
        ModelMessage(role='system', content='Be concise.'),
        ModelMessage(role='user', content='Hello'),
    )
    assert model_service.stream_calls[0][2]['stop'] == ['END']


@pytest.mark.asyncio
async def test_invalid_completion_snapshot_fails_before_runtime_stream_creation() -> None:
    coordinator = FakeCoordinator()
    model_service = FakeModelService()
    compat = OpenAICompatService(
        coordinator,
        model_service,
        completion_id_factory=lambda: 'invalid-private-id',
        clock=lambda: CREATED_AT,
    )

    with pytest.raises(PublicApiError) as raised:
        await compat.stream(identity(), request(stream=True))

    assert raised.value.code == 'service_unavailable'
    assert model_service.stream_calls == []
    assert coordinator.resolve_calls == []


@pytest.mark.asyncio
async def test_entitlement_error_is_preserved_without_runtime_call() -> None:
    compat, coordinator, model_service = service()
    expected = PublicApiError.model_not_found()
    coordinator.error = expected

    with pytest.raises(PublicApiError) as raised:
        await compat.complete(identity(), request())

    assert raised.value.code == expected.code
    assert raised.value is not expected
    assert model_service.chat_calls == []


@pytest.mark.asyncio
async def test_complete_and_stream_reject_mismatched_modes_before_entitlement() -> None:
    compat, coordinator, _ = service()

    with pytest.raises(PublicApiError, match='invalid') as complete_error:
        await compat.complete(identity(), request(stream=True))
    with pytest.raises(PublicApiError, match='invalid') as stream_error:
        await compat.stream(identity(), request(stream=False))

    assert complete_error.value.code == 'invalid_request'
    assert stream_error.value.code == 'invalid_request'
    assert coordinator.resolve_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('model_error', 'public_code'),
    (
        (ModelServiceError.not_found(), 'model_not_found'),
        (ModelServiceError.disabled(), 'model_not_found'),
        (ModelServiceError.binding_missing(), 'service_unavailable'),
        (ModelServiceError.runtime_misconfigured(), 'service_unavailable'),
        (ModelServiceError.runtime_unavailable(), 'service_unavailable'),
        (ModelServiceError.runtime_timeout(), 'service_unavailable'),
        (ModelServiceError.provider_auth_failed(), 'upstream_error'),
        (ModelServiceError.provider_rate_limited(), 'upstream_error'),
        (ModelServiceError.runtime_error(), 'upstream_error'),
    ),
)
async def test_model_service_errors_map_to_fixed_public_errors(
    model_error: ModelServiceError,
    public_code: str,
) -> None:
    compat, _, model_service = service()
    model_service.error = model_error

    with pytest.raises(PublicApiError) as raised:
        await compat.complete(identity(), request())

    assert raised.value.code == public_code
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_public_error_traceback_does_not_retain_request_or_service_objects() -> None:
    compat, _, model_service = service()
    model_service.error = ModelServiceError.runtime_error()
    sensitive_request = request()
    sensitive_request.messages[1].content = 'PRIVATE prompt body'

    with pytest.raises(PublicApiError) as raised:
        await compat.complete(identity(), sensitive_request)

    traceback = raised.value.__traceback__
    while traceback is not None:
        if (
            '/plugins/ai/' in traceback.tb_frame.f_code.co_filename
            and '/tests/' not in traceback.tb_frame.f_code.co_filename
        ):
            serialized = repr(traceback.tb_frame.f_locals)
            assert 'PRIVATE prompt body' not in serialized
            assert MODEL_ID not in serialized
            assert ENGINE_MODEL not in serialized
        traceback = traceback.tb_next


@pytest.mark.asyncio
async def test_cancellation_is_preserved() -> None:
    compat, _, model_service = service()
    model_service.error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await compat.stream(identity(), request(stream=True))


@pytest.mark.asyncio
async def test_embed_goes_through_coordinator_and_returns_openai_shape() -> None:
    compat, coordinator, model_service = service()

    response = await compat.embed(identity(), embed_request())

    assert coordinator.resolve_calls == [(identity(), MODEL_CODE, 'embedding')]
    assert model_service.embed_calls == [
        (
            MODEL_ID,
            ('first document', 'second document'),
            {'dimensions': 1024, 'user': 'external-user-42'},
        )
    ]
    assert response.model_dump() == {
        'object': 'list',
        'data': [{'object': 'embedding', 'index': 0, 'embedding': [0.1, 0.2]}],
        'model': MODEL_CODE,
        'usage': {'prompt_tokens': 3, 'total_tokens': 3},
    }
    assert MODEL_ID not in repr(response.model_dump())
    assert ENGINE_MODEL not in repr(response.model_dump())


@pytest.mark.asyncio
async def test_rerank_goes_through_coordinator_and_rebuilds_documents_from_request_snapshot() -> None:
    compat, coordinator, model_service = service()

    response = await compat.rerank(identity(), rerank_request())

    assert coordinator.resolve_calls == [(identity(), MODEL_CODE, 'rerank')]
    assert model_service.rerank_calls == [
        (
            MODEL_ID,
            'Which document describes the refund policy?',
            ('Shipping usually takes three days.', 'Refunds are accepted within thirty days.'),
            {'top_n': 2, 'return_documents': True},
        )
    ]
    assert response.model_dump() == {
        'id': 'rrk_PRIVATE',
        'results': [
            {
                'index': 0,
                'relevance_score': 0.9,
                'document': {'text': 'Shipping usually takes three days.'},
            }
        ],
        'meta': {'billed_units': {'search_units': 1}, 'tokens': {'input_tokens': 3}},
    }
    assert MODEL_ID not in repr(response.model_dump())
    assert ENGINE_MODEL not in repr(response.model_dump())


@pytest.mark.asyncio
@pytest.mark.parametrize('return_documents', [False, None])
async def test_rerank_omits_document_text_unless_requested(return_documents: bool | None) -> None:
    compat, _, _ = service()
    request = rerank_request().model_copy(update={'return_documents': return_documents})
    response = await compat.rerank(identity(), request)
    assert all(item.document is None for item in response.results)
    assert 'Shipping usually takes three days.' not in response.model_dump_json()
    assert 'Refunds are accepted within thirty days.' not in response.model_dump_json()
