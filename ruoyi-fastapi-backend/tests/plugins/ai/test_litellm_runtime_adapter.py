import asyncio
import traceback
from collections.abc import AsyncIterator, Mapping
from dataclasses import FrozenInstanceError
from decimal import Decimal
from typing import Any

import httpx
import pytest

from plugins.ai.config import LiteLLMSettings
from plugins.ai.domain.model_runtime import ModelChatRequest, ModelMessage, ModelStreamEvent, ModelUsage
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient, RuntimeChatResult
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
    LiteLLMMisconfiguredError,
    LiteLLMProviderAuthenticationError,
    LiteLLMProviderRateLimitError,
    LiteLLMTimeoutError,
    LiteLLMUnavailableError,
    LiteLLMUpstreamError,
)
from plugins.ai.infrastructure.litellm_runtime_adapter import LiteLLMRuntimeAdapter

CACHE_DETAIL_READ_TOKENS = 5
CACHE_DETAIL_WRITE_TOKENS = 2
CACHE_FALLBACK_READ_TOKENS = 7
CACHE_FALLBACK_WRITE_TOKENS = 4


class FakeRuntimeClient:
    def __init__(
        self,
        *,
        chat_response: Mapping[str, object] | None = None,
        models_response: Mapping[str, object] | None = None,
        stream_chunks: tuple[bytes, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.chat_response = (
            chat_response
            if chat_response is not None
            else {
                'id': 'chatcmpl-1',
                'choices': [{'message': {'content': 'hello'}}],
            }
        )
        self.models_response = models_response if models_response is not None else {'data': []}
        self.stream_chunks = stream_chunks
        self.error = error
        self.chat_payloads: list[Mapping[str, object]] = []
        self.stream_payloads: list[Mapping[str, object]] = []
        self.last_stream: RecordingByteStream | None = None

    async def list_models(self) -> Mapping[str, object]:
        if self.error is not None:
            raise self.error
        return self.models_response

    async def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.chat_payloads.append(payload)
        if self.error is not None:
            raise self.error
        return RuntimeChatResult(payload=self.chat_response)

    def stream_chat_completion(self, payload: Mapping[str, object]) -> AsyncIterator[bytes]:
        self.stream_payloads.append(payload)
        self.last_stream = RecordingByteStream(self.stream_chunks, error=self.error)
        return self.last_stream


class RecordingByteStream:
    def __init__(self, chunks: tuple[bytes, ...], *, error: Exception | None = None) -> None:
        self._chunks = iter(chunks)
        self._error = error
        self.closed = False

    def __aiter__(self) -> 'RecordingByteStream':
        return self

    async def __anext__(self) -> bytes:
        if self._error is not None:
            raise self._error
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed = True


def test_model_chat_request_takes_an_immutable_snapshot_of_nested_inputs() -> None:
    content = [{'type': 'text', 'text': 'original'}]
    messages = [ModelMessage(role='user', content=content)]
    parameters: dict[str, Any] = {'temperature': 0.2, 'stop': ['before-mutation']}

    request = ModelChatRequest(engine_model='company-chat', messages=messages, parameters=parameters)
    content[0]['text'] = 'mutated'
    messages.append(ModelMessage(role='assistant', content='late'))
    parameters['temperature'] = 1.5
    parameters['stop'].append('late')

    assert request.messages == (ModelMessage(role='user', content=({'type': 'text', 'text': 'original'},)),)
    assert dict(request.parameters) == {'temperature': 0.2, 'stop': ('before-mutation',)}
    with pytest.raises(TypeError):
        request.parameters['temperature'] = 1.0
    with pytest.raises(FrozenInstanceError):
        request.engine_model = 'other'


@pytest.mark.parametrize('engine_model', ('', '   ', ' company-chat', 'company-chat '))
def test_model_chat_request_rejects_empty_or_padded_engine_model(engine_model: str) -> None:
    with pytest.raises(ValueError):
        ModelChatRequest(
            engine_model=engine_model,
            messages=(ModelMessage(role='user', content='hello'),),
        )


@pytest.mark.parametrize('role', ('', 'tool', 'USER', ' user'))
def test_model_message_rejects_unsupported_role(role: str) -> None:
    with pytest.raises(ValueError):
        ModelMessage(role=role, content='hello')


@pytest.mark.parametrize('role', ('system', 'developer', 'user', 'assistant'))
def test_model_message_accepts_supported_openai_roles(role: str) -> None:
    assert ModelMessage(role=role, content='hello').role == role


@pytest.mark.parametrize(
    'parameters',
    (
        {1: 'non-string-key'},
        {'nested': {1: 'non-string-key'}},
        {'not_json': object()},
    ),
)
def test_model_chat_request_rejects_non_json_parameter_structure(parameters: Mapping[object, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        ModelChatRequest(
            engine_model='company-chat',
            messages=(ModelMessage(role='user', content='hello'),),
            parameters=parameters,
        )


@pytest.mark.parametrize(
    'content',
    (
        object(),
        {'type': 'text'},
        [object()],
        [{1: 'non-string-key'}],
    ),
)
def test_model_message_rejects_invalid_content(content: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        ModelMessage(role='user', content=content)


def test_model_chat_request_requires_at_least_one_message() -> None:
    with pytest.raises(ValueError):
        ModelChatRequest(engine_model='company-chat', messages=())


@pytest.mark.parametrize(
    'kwargs',
    (
        {'input_tokens': -1},
        {'output_tokens': True},
        {'reasoning_tokens': 1.5},
        {'cache_read_tokens': -1},
        {'cache_write_tokens': -1},
        {'raw_cost': Decimal('-0.1')},
        {'raw_cost': Decimal('NaN')},
    ),
)
def test_model_usage_rejects_invalid_token_counts_and_cost(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        ModelUsage(**kwargs)


def test_model_stream_event_takes_bytes_snapshot() -> None:
    source = bytearray(b'original')

    event = ModelStreamEvent(data=source)
    source[:] = b'mutated!'

    assert event.data == b'original'
    with pytest.raises(FrozenInstanceError):
        event.data = b'other'


@pytest.mark.asyncio
async def test_real_runtime_client_headers_are_normalized_by_adapter() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={'id': 'body-id', 'choices': [{'message': {'content': 'answer'}}]},
            headers={
                'x-litellm-call-id': 'runtime-call-1',
                'x-litellm-response-cost': '0.00125',
            },
        )

    settings = LiteLLMSettings(
        LITELLM_BASE_URL='https://proxy.example.com',
        LITELLM_RUNTIME_KEY='runtime-secret',
    )
    client = LiteLLMRuntimeClient(settings, transport=httpx.MockTransport(handler))
    try:
        response = await LiteLLMRuntimeAdapter(client).chat(
            ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))
        )
    finally:
        await client.aclose()

    assert response.request_id == 'runtime-call-1'
    assert response.usage.raw_cost == Decimal('0.00125')


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_cost', ('', 'not-a-number', 'NaN', 'Infinity', '-0.1'))
async def test_invalid_runtime_cost_header_maps_to_domain_error(invalid_cost: str) -> None:
    client = FakeRuntimeClient()
    result = RuntimeChatResult(payload=client.chat_response, response_cost=invalid_cost)
    client.create_chat_completion = lambda payload: _async_result(result)  # type: ignore[method-assign]

    with pytest.raises(ModelServiceError) as raised:
        await LiteLLMRuntimeAdapter(client).chat(
            ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))
        )

    assert raised.value.code == 'MODEL_RUNTIME_ERROR'


async def _async_result(value: RuntimeChatResult) -> RuntimeChatResult:
    return value


@pytest.mark.asyncio
async def test_chat_maps_only_platform_parameter_allowlist_to_openai_payload() -> None:
    client = FakeRuntimeClient()
    adapter = LiteLLMRuntimeAdapter(client)
    request = ModelChatRequest(
        engine_model='company-chat',
        messages=(ModelMessage(role='user', content='hello'),),
        parameters={
            'temperature': 0.2,
            'top_p': 0.9,
            'max_tokens': 100,
            'presence_penalty': 0.1,
            'frequency_penalty': -0.1,
        },
    )

    await adapter.chat(request)

    assert client.chat_payloads == [
        {
            'model': 'company-chat',
            'messages': [{'role': 'user', 'content': 'hello'}],
            'temperature': 0.2,
            'top_p': 0.9,
            'max_tokens': 100,
            'presence_penalty': 0.1,
            'frequency_penalty': -0.1,
            'stream': False,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('reserved_key', ('model', 'messages', 'stream'))
async def test_chat_rejects_parameters_that_try_to_override_adapter_owned_fields(reserved_key: str) -> None:
    client = FakeRuntimeClient()
    adapter = LiteLLMRuntimeAdapter(client)
    request = ModelChatRequest(
        engine_model='company-chat',
        messages=(ModelMessage(role='user', content='hello'),),
        parameters={reserved_key: 'attacker-controlled'},
    )

    with pytest.raises(ValueError, match='不支持的模型参数'):
        await adapter.chat(request)

    assert client.chat_payloads == []


@pytest.mark.asyncio
async def test_chat_normalizes_text_reasoning_usage_request_id_and_cost() -> None:
    client = FakeRuntimeClient(
        chat_response={
            'id': 'chatcmpl-123',
            'choices': [
                {
                    'message': {
                        'content': 'final answer',
                        'reasoning_content': 'private chain summary',
                        'provider_extra': 'ignored',
                    }
                }
            ],
            'usage': {
                'prompt_tokens': 12,
                'completion_tokens': 7,
                'completion_tokens_details': {'reasoning_tokens': 3},
                'prompt_tokens_details': {
                    'cached_tokens': 5,
                    'cache_creation_tokens': 2,
                },
                'unknown_metric': 999,
            },
            '_hidden_params': {'response_cost': 0.00125, 'api_key': 'must-not-survive'},
            'provider_response': {'secret': 'must-not-survive'},
        }
    )
    adapter = LiteLLMRuntimeAdapter(client)

    response = await adapter.chat(
        ModelChatRequest(
            engine_model='company-chat',
            messages=(ModelMessage(role='user', content='hello'),),
        )
    )

    assert response.text == 'final answer'
    assert response.reasoning == 'private chain summary'
    assert response.request_id == 'chatcmpl-123'
    assert response.usage == ModelUsage(
        input_tokens=12,
        output_tokens=7,
        reasoning_tokens=3,
        cache_read_tokens=5,
        cache_write_tokens=2,
        raw_cost=Decimal('0.00125'),
    )
    assert not hasattr(response, 'raw_response')
    assert 'must-not-survive' not in repr(response)


@pytest.mark.asyncio
async def test_usage_cache_details_take_precedence_over_top_level_fallbacks() -> None:
    client = FakeRuntimeClient(
        chat_response={
            'choices': [{'message': {'content': 'answer'}}],
            'usage': {
                'prompt_tokens_details': {
                    'cached_tokens': CACHE_DETAIL_READ_TOKENS,
                    'cache_creation_tokens': CACHE_DETAIL_WRITE_TOKENS,
                },
                'cache_read_input_tokens': 99,
                'cache_creation_input_tokens': 98,
            },
        }
    )

    response = await LiteLLMRuntimeAdapter(client).chat(
        ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))
    )

    assert response.usage.cache_read_tokens == CACHE_DETAIL_READ_TOKENS
    assert response.usage.cache_write_tokens == CACHE_DETAIL_WRITE_TOKENS


@pytest.mark.asyncio
async def test_usage_cache_tokens_fall_back_to_stable_top_level_fields() -> None:
    client = FakeRuntimeClient(
        chat_response={
            'choices': [{'message': {'content': 'answer'}}],
            'usage': {
                'cache_read_input_tokens': CACHE_FALLBACK_READ_TOKENS,
                'cache_creation_input_tokens': CACHE_FALLBACK_WRITE_TOKENS,
            },
        }
    )

    response = await LiteLLMRuntimeAdapter(client).chat(
        ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))
    )

    assert response.usage.cache_read_tokens == CACHE_FALLBACK_READ_TOKENS
    assert response.usage.cache_write_tokens == CACHE_FALLBACK_WRITE_TOKENS


@pytest.mark.asyncio
async def test_chat_uses_stable_defaults_for_missing_optional_response_fields() -> None:
    adapter = LiteLLMRuntimeAdapter(FakeRuntimeClient())

    response = await adapter.chat(
        ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))
    )

    assert response.text == 'hello'
    assert response.reasoning is None
    assert response.request_id == 'chatcmpl-1'
    assert response.usage == ModelUsage()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'malformed_response',
    (
        {},
        {'choices': []},
        {'choices': [{'message': 'not-an-object'}]},
        {'choices': [{'message': {'content': 123}}]},
    ),
)
async def test_chat_maps_malformed_response_to_sanitized_runtime_error(
    malformed_response: Mapping[str, object],
) -> None:
    adapter = LiteLLMRuntimeAdapter(FakeRuntimeClient(chat_response=malformed_response))

    with pytest.raises(ModelServiceError) as raised:
        await adapter.chat(
            ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))
        )

    assert raised.value.public_dict() == {
        'code': 'MODEL_RUNTIME_ERROR',
        'message': '模型运行时调用失败',
    }
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_list_model_ids_returns_only_trimmed_unique_nonempty_string_ids() -> None:
    client = FakeRuntimeClient(
        models_response={
            'data': [
                {'id': ' company-chat '},
                {'id': 'company-chat'},
                {'id': 'embedding-v1'},
                {'id': ''},
                {'id': '   '},
                {'id': None},
                {'id': 123},
                None,
                'not-an-object',
            ]
        }
    )

    assert await LiteLLMRuntimeAdapter(client).list_model_ids() == {'company-chat', 'embedding-v1'}


@pytest.mark.asyncio
@pytest.mark.parametrize('response', ({}, {'data': None}, {'data': {}}, {'data': 'not-a-list'}))
async def test_list_model_ids_rejects_malformed_top_level_data(response: Mapping[str, object]) -> None:
    with pytest.raises(ModelServiceError) as raised:
        await LiteLLMRuntimeAdapter(FakeRuntimeClient(models_response=response)).list_model_ids()

    assert raised.value.code == 'MODEL_RUNTIME_ERROR'
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_stream_chat_delegates_bytes_as_engine_neutral_events_and_controls_stream_flag() -> None:
    client = FakeRuntimeClient(stream_chunks=(b'data: one\n\n', b'data: [DONE]\n\n'))
    adapter = LiteLLMRuntimeAdapter(client)
    request = ModelChatRequest(
        engine_model='company-chat',
        messages=(ModelMessage(role='user', content='hello'),),
        parameters={'temperature': 0.2},
    )

    events = [event async for event in adapter.stream_chat(request)]

    assert events == [ModelStreamEvent(data=b'data: one\n\n'), ModelStreamEvent(data=b'data: [DONE]\n\n')]
    assert client.stream_payloads == [
        {
            'model': 'company-chat',
            'messages': [{'role': 'user', 'content': 'hello'}],
            'temperature': 0.2,
            'stream': True,
        }
    ]
    assert client.last_stream is not None
    assert client.last_stream.closed is True


@pytest.mark.asyncio
async def test_closing_adapter_stream_closes_underlying_runtime_stream() -> None:
    client = FakeRuntimeClient(stream_chunks=(b'first', b'second'))
    adapter_stream = LiteLLMRuntimeAdapter(client).stream_chat(
        ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))
    )

    assert await anext(adapter_stream) == ModelStreamEvent(data=b'first')
    assert client.last_stream is not None
    assert client.last_stream.closed is False
    await adapter_stream.aclose()

    assert client.last_stream.closed is True


@pytest.mark.asyncio
async def test_stream_chat_does_not_map_cancellation_to_domain_error() -> None:
    client = FakeRuntimeClient(error=asyncio.CancelledError())
    stream = LiteLLMRuntimeAdapter(client).stream_chat(
        ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))
    )

    with pytest.raises(asyncio.CancelledError):
        await anext(stream)

    assert client.last_stream is not None
    assert client.last_stream.closed is True


ERROR_MAPPINGS = (
    (LiteLLMMisconfiguredError, 'MODEL_RUNTIME_MISCONFIGURED'),
    (LiteLLMUnavailableError, 'MODEL_RUNTIME_UNAVAILABLE'),
    (LiteLLMTimeoutError, 'MODEL_RUNTIME_TIMEOUT'),
    (LiteLLMAuthenticationError, 'MODEL_RUNTIME_AUTH_FAILED'),
    (LiteLLMProviderAuthenticationError, 'MODEL_PROVIDER_AUTH_FAILED'),
    (LiteLLMProviderRateLimitError, 'MODEL_PROVIDER_RATE_LIMITED'),
    (LiteLLMUpstreamError, 'MODEL_RUNTIME_ERROR'),
)


def _sensitive_error(error_type: type[Exception]) -> Exception:
    return error_type('Authorization: Bearer provider-secret at https://runtime.internal private-body')


def _assert_sanitized_domain_error(error: ModelServiceError, expected_code: str) -> None:
    serialized = repr(
        (
            str(error),
            repr(error),
            vars(error),
            error.args,
            error.public_dict(),
            error.__cause__,
            error.__context__,
            ''.join(traceback.format_exception(error)),
        )
    ).lower()
    assert error.code == expected_code
    assert error.__cause__ is None
    assert error.__context__ is None
    assert 'authorization' not in serialized
    assert 'provider-secret' not in serialized
    assert 'https://' not in serialized
    assert 'private-body' not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(('error_type', 'expected_code'), ERROR_MAPPINGS)
async def test_chat_maps_runtime_errors_to_sanitized_model_service_error(
    error_type: type[Exception],
    expected_code: str,
) -> None:
    adapter = LiteLLMRuntimeAdapter(FakeRuntimeClient(error=_sensitive_error(error_type)))
    request = ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))

    with pytest.raises(ModelServiceError) as raised:
        await adapter.chat(request)

    _assert_sanitized_domain_error(raised.value, expected_code)


@pytest.mark.asyncio
@pytest.mark.parametrize(('error_type', 'expected_code'), ERROR_MAPPINGS)
async def test_list_models_maps_runtime_errors_to_sanitized_model_service_error(
    error_type: type[Exception],
    expected_code: str,
) -> None:
    adapter = LiteLLMRuntimeAdapter(FakeRuntimeClient(error=_sensitive_error(error_type)))

    with pytest.raises(ModelServiceError) as raised:
        await adapter.list_model_ids()

    _assert_sanitized_domain_error(raised.value, expected_code)


@pytest.mark.asyncio
@pytest.mark.parametrize(('error_type', 'expected_code'), ERROR_MAPPINGS)
async def test_stream_chat_maps_runtime_errors_to_sanitized_model_service_error(
    error_type: type[Exception],
    expected_code: str,
) -> None:
    adapter = LiteLLMRuntimeAdapter(FakeRuntimeClient(error=_sensitive_error(error_type)))
    request = ModelChatRequest(engine_model='company-chat', messages=(ModelMessage(role='user', content='hello'),))
    stream = adapter.stream_chat(request)

    with pytest.raises(ModelServiceError) as raised:
        await anext(stream)

    _assert_sanitized_domain_error(raised.value, expected_code)
