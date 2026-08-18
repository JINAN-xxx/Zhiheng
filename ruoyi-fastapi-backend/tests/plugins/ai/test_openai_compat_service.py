import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

import pytest

from plugins.ai.domain.model_runtime import ModelChatResponse, ModelMessage, ModelStreamEvent, ModelUsage
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.entity.vo.ai_openai_compat_vo import OpenAIChatCompletionRequestModel
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity
from plugins.ai.infrastructure.openai_stream_adapter import OpenAIStreamAdapter
from plugins.ai.service.api_entitlement_service import AuthorizedPublicModel
from plugins.ai.service.model_service import ModelServiceChatResult, ModelServiceStreamResult
from plugins.ai.service.openai_compat_service import OpenAICompatService

APPLICATION_ID = 'app_00000000000040008000000000000001'
KEY_ID = 'key_00000000000040008000000000000001'
MODEL_ID = 'mdl_00000000000040008000000000000001'
BINDING_ID = 'bnd_00000000000040008000000000000001'
MODEL_CODE = 'deepseek-chat'
ENGINE_MODEL = 'runtime-deepseek-chat'
COMPLETION_ID = 'chatcmpl_00000000000040008000000000000001'
CREATED_AT = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
CREATED = int(CREATED_AT.timestamp())


class EmptyStream:
    def __aiter__(self) -> 'EmptyStream':
        return self

    async def __anext__(self) -> ModelStreamEvent:
        raise StopAsyncIteration

    async def aclose(self) -> None:
        return None


class FakeEntitlementService:
    def __init__(self) -> None:
        self.calls: list[tuple[DeveloperApiIdentity, str]] = []
        self.error: BaseException | None = None

    async def resolve_model(
        self,
        identity: DeveloperApiIdentity,
        model_code: str,
    ) -> AuthorizedPublicModel:
        self.calls.append((identity, model_code))
        if self.error is not None:
            raise self.error
        return AuthorizedPublicModel(platform_model_id=MODEL_ID, model_code=MODEL_CODE)


class FakeModelService:
    def __init__(self) -> None:
        self.chat_calls: list[tuple[str, Sequence[ModelMessage], Mapping[str, object]]] = []
        self.stream_calls: list[tuple[str, Sequence[ModelMessage], Mapping[str, object]]] = []
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


def service() -> tuple[OpenAICompatService, FakeEntitlementService, FakeModelService]:
    entitlement = FakeEntitlementService()
    model_service = FakeModelService()
    return (
        OpenAICompatService(
            entitlement,
            model_service,
            completion_id_factory=lambda: COMPLETION_ID,
            clock=lambda: CREATED_AT,
        ),
        entitlement,
        model_service,
    )


@pytest.mark.asyncio
async def test_complete_resolves_public_code_calls_platform_model_and_returns_public_shape() -> None:
    compat, entitlement, model_service = service()

    response = await compat.complete(identity(), request())

    assert entitlement.calls == [(identity(), MODEL_CODE)]
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
    compat, entitlement, model_service = service()

    result = await compat.stream(identity(), request(stream=True))

    assert type(result) is OpenAIStreamAdapter
    assert entitlement.calls == [(identity(), MODEL_CODE)]
    assert model_service.stream_calls[0][0] == MODEL_ID
    assert model_service.stream_calls[0][1] == (
        ModelMessage(role='system', content='Be concise.'),
        ModelMessage(role='user', content='Hello'),
    )
    assert model_service.stream_calls[0][2]['stop'] == ['END']


@pytest.mark.asyncio
async def test_invalid_completion_snapshot_fails_before_runtime_stream_creation() -> None:
    entitlement = FakeEntitlementService()
    model_service = FakeModelService()
    compat = OpenAICompatService(
        entitlement,
        model_service,
        completion_id_factory=lambda: 'invalid-private-id',
        clock=lambda: CREATED_AT,
    )

    with pytest.raises(PublicApiError) as raised:
        await compat.stream(identity(), request(stream=True))

    assert raised.value.code == 'service_unavailable'
    assert model_service.stream_calls == []


@pytest.mark.asyncio
async def test_entitlement_error_is_preserved_without_runtime_call() -> None:
    compat, entitlement, model_service = service()
    expected = PublicApiError.model_not_found()
    entitlement.error = expected

    with pytest.raises(PublicApiError) as raised:
        await compat.complete(identity(), request())

    assert raised.value.code == expected.code
    assert raised.value is not expected
    assert model_service.chat_calls == []


@pytest.mark.asyncio
async def test_complete_and_stream_reject_mismatched_modes_before_entitlement() -> None:
    compat, entitlement, _ = service()

    with pytest.raises(PublicApiError, match='invalid') as complete_error:
        await compat.complete(identity(), request(stream=True))
    with pytest.raises(PublicApiError, match='invalid') as stream_error:
        await compat.stream(identity(), request(stream=False))

    assert complete_error.value.code == 'invalid_request'
    assert stream_error.value.code == 'invalid_request'
    assert entitlement.calls == []


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
