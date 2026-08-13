import sys
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Protocol, TypeVar

from plugins.ai.domain.model_runtime import (
    ModelChatRequest,
    ModelChatResponse,
    ModelMessage,
    ModelStreamEvent,
    ModelUsage,
)
from plugins.ai.domain.model_service_errors import ModelServiceError, call_with_model_service_error
from plugins.ai.infrastructure.litellm_client import RuntimeChatResult
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
    LiteLLMError,
    LiteLLMMisconfiguredError,
    LiteLLMProviderAuthenticationError,
    LiteLLMProviderRateLimitError,
    LiteLLMTimeoutError,
    LiteLLMUnavailableError,
)

_ALLOWED_PARAMETERS = frozenset(
    {
        'temperature',
        'top_p',
        'max_tokens',
        'presence_penalty',
        'frequency_penalty',
    }
)
_FORBIDDEN_MODEL_ID_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})
_ERROR_FACTORIES: dict[type[LiteLLMError], Callable[[], ModelServiceError]] = {
    LiteLLMMisconfiguredError: ModelServiceError.runtime_misconfigured,
    LiteLLMUnavailableError: ModelServiceError.runtime_unavailable,
    LiteLLMTimeoutError: ModelServiceError.runtime_timeout,
    LiteLLMAuthenticationError: ModelServiceError.runtime_auth_failed,
    LiteLLMProviderAuthenticationError: ModelServiceError.provider_auth_failed,
    LiteLLMProviderRateLimitError: ModelServiceError.provider_rate_limited,
}
ResultT = TypeVar('ResultT')


class RuntimeClient(Protocol):
    async def list_models(self) -> Mapping[str, object]: ...

    async def create_chat_completion(self, payload: Mapping[str, object]) -> RuntimeChatResult: ...

    def stream_chat_completion(self, payload: Mapping[str, object]) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_thaw(item) for item in value]
    return value


class LiteLLMRuntimeAdapter:
    def __init__(self, client: RuntimeClient) -> None:
        self._client = client

    async def chat(self, request: ModelChatRequest) -> ModelChatResponse:
        payload = self._build_chat_payload(request, stream=False)
        result = await _call_runtime(lambda: self._client.create_chat_completion(payload))
        normalized = self._normalize_chat_response(result)
        del result
        return await _unwrap_chat_response(normalized)

    async def list_model_ids(self) -> set[str]:
        response = await _call_runtime(self._client.list_models)
        model_ids = self._normalize_model_ids(response)
        del response
        return await _unwrap_model_ids(model_ids)

    @staticmethod
    def _normalize_model_ids(response: Mapping[str, object]) -> set[str] | None:
        data = response.get('data')
        if not isinstance(data, list):
            return None
        model_ids: set[str] = set()
        for item in data:
            if not isinstance(item, Mapping):
                continue
            model_id = item.get('id')
            if not isinstance(model_id, str):
                continue
            if any(unicodedata.category(character) in _FORBIDDEN_MODEL_ID_CATEGORIES for character in model_id):
                continue
            normalized_id = model_id.strip()
            if normalized_id:
                model_ids.add(normalized_id)
        return model_ids

    async def aclose(self) -> None:
        await _call_runtime(self._client.aclose)

    async def stream_chat(self, request: ModelChatRequest) -> AsyncIterator[ModelStreamEvent]:
        payload = self._build_chat_payload(request, stream=True)
        stream = await _create_runtime_stream(lambda: self._client.stream_chat_completion(payload))
        try:
            while True:
                completed, chunk = await _next_runtime_chunk(stream)
                if completed:
                    return
                yield ModelStreamEvent(data=bytes(chunk))
        finally:
            close = getattr(stream, 'aclose', None)
            if close is not None:
                if sys.exc_info()[0] is None:
                    await _call_runtime(close)
                else:
                    try:
                        await close()
                    except Exception:
                        pass

    @staticmethod
    def _build_chat_payload(request: ModelChatRequest, *, stream: bool) -> dict[str, object]:
        unsupported = set(request.parameters).difference(_ALLOWED_PARAMETERS)
        if unsupported:
            raise ValueError('不支持的模型参数')
        messages = [LiteLLMRuntimeAdapter._message_payload(message) for message in request.messages]
        return {
            'model': request.engine_model,
            'messages': messages,
            **{key: _thaw(value) for key, value in request.parameters.items()},
            'stream': stream,
        }

    @staticmethod
    def _message_payload(message: ModelMessage) -> dict[str, object]:
        return {'role': message.role, 'content': _thaw(message.content)}

    @staticmethod
    def _normalize_chat_response(result: RuntimeChatResult) -> ModelChatResponse | None:
        response = result.payload
        choices = response.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            return None
        message = choices[0].get('message')
        if not isinstance(message, Mapping):
            return None
        text = message.get('content')
        if not isinstance(text, str):
            return None

        usage = LiteLLMRuntimeAdapter._normalize_usage(response, response_cost=result.response_cost)
        if usage is None:
            return None
        reasoning_value = message.get('reasoning_content', message.get('reasoning'))
        reasoning = reasoning_value if isinstance(reasoning_value, str) else None
        request_id_value = result.request_id or response.get('id')
        request_id = request_id_value if isinstance(request_id_value, str) and request_id_value else None
        return ModelChatResponse(text=text, reasoning=reasoning, usage=usage, request_id=request_id)

    @staticmethod
    def _normalize_usage(response: Mapping[str, object], *, response_cost: str | None) -> ModelUsage | None:
        usage = response.get('usage')
        if usage is None:
            usage = {}
        if not isinstance(usage, Mapping):
            return None

        completion_details = usage.get('completion_tokens_details', {})
        prompt_details = usage.get('prompt_tokens_details', {})
        if completion_details is None:
            completion_details = {}
        if prompt_details is None:
            prompt_details = {}
        if not isinstance(completion_details, Mapping) or not isinstance(prompt_details, Mapping):
            return None

        valid_cost, cost = LiteLLMRuntimeAdapter._optional_cost(response_cost)
        cache_read = prompt_details.get('cached_tokens', usage.get('cache_read_input_tokens', 0))
        cache_write = prompt_details.get('cache_creation_tokens', usage.get('cache_creation_input_tokens', 0))
        input_tokens = LiteLLMRuntimeAdapter._token_count(usage.get('prompt_tokens', 0))
        output_tokens = LiteLLMRuntimeAdapter._token_count(usage.get('completion_tokens', 0))
        reasoning_tokens = LiteLLMRuntimeAdapter._token_count(completion_details.get('reasoning_tokens', 0))
        cache_read_tokens = LiteLLMRuntimeAdapter._token_count(cache_read)
        cache_write_tokens = LiteLLMRuntimeAdapter._token_count(cache_write)
        if (
            not valid_cost
            or input_tokens is None
            or output_tokens is None
            or reasoning_tokens is None
            or cache_read_tokens is None
            or cache_write_tokens is None
        ):
            return None
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            raw_cost=cost,
        )

    @staticmethod
    def _token_count(value: object) -> int | None:
        if type(value) is not int or value < 0:
            return None
        return value

    @staticmethod
    def _optional_cost(value: object) -> tuple[bool, Decimal | None]:
        if value is None:
            return True, None
        if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
            return False, None
        try:
            cost = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return False, None
        if not cost.is_finite() or cost < 0:
            return False, None
        return True, cost


def _error_factory(error: LiteLLMError) -> Callable[[], ModelServiceError]:
    return _ERROR_FACTORIES.get(type(error), ModelServiceError.runtime_error)


async def _call_runtime(operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
    try:
        return await operation()
    except LiteLLMError as error:
        factory = _error_factory(error)
    return await call_with_model_service_error(_raise_runtime_failure, factory)


async def _create_runtime_stream(operation: Callable[[], AsyncIterator[bytes]]) -> AsyncIterator[bytes]:
    try:
        return operation()
    except LiteLLMError as error:
        factory = _error_factory(error)
    return await call_with_model_service_error(_raise_runtime_failure, factory)


async def _raise_runtime_failure() -> ResultT:
    raise RuntimeError


async def _unwrap_chat_response(response: ModelChatResponse | None) -> ModelChatResponse:
    if response is None:
        raise ModelServiceError.runtime_error()
    return response


async def _unwrap_model_ids(model_ids: set[str] | None) -> set[str]:
    if model_ids is None:
        raise ModelServiceError.runtime_error()
    return model_ids


async def _read_runtime_chunk(stream: AsyncIterator[bytes]) -> tuple[bool, bytes]:
    try:
        return False, await anext(stream)
    except StopAsyncIteration:
        return True, b''


async def _next_runtime_chunk(stream: AsyncIterator[bytes]) -> tuple[bool, bytes]:
    try:
        return await _read_runtime_chunk(stream)
    except LiteLLMError as error:
        factory = _error_factory(error)
    await call_with_model_service_error(_raise_runtime_failure, factory)
    raise AssertionError('不可达')
