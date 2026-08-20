import sys
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Protocol, TypeVar
from uuid import uuid4

from plugins.ai.domain.model_runtime import (
    ModelChatRequest,
    ModelChatResponse,
    ModelEmbeddingRequest,
    ModelEmbeddingResponse,
    ModelEmbeddingVector,
    ModelMessage,
    ModelRerankDocument,
    ModelRerankRequest,
    ModelRerankResponse,
    ModelRerankResult,
    ModelStreamEvent,
    ModelUsage,
)
from plugins.ai.domain.model_service_errors import ModelServiceError, call_with_model_service_error
from plugins.ai.infrastructure.litellm_client import RuntimeChatResult, RuntimeRerankResult
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
    LiteLLMError,
    LiteLLMMisconfiguredError,
    LiteLLMProviderAuthenticationError,
    LiteLLMProviderRateLimitError,
    LiteLLMTimeoutError,
    LiteLLMUnavailableError,
)
from plugins.ai.infrastructure.platform_sse import PlatformSSEError, PlatformSSEEvent, parse_platform_sse

_ALLOWED_PARAMETERS = frozenset(
    {
        'temperature',
        'top_p',
        'max_tokens',
        'presence_penalty',
        'frequency_penalty',
        'stop',
        'user',
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

    async def create_embedding(
        self,
        payload: Mapping[str, object],
        *,
        max_json_response_bytes: int | None = None,
    ) -> Mapping[str, object]: ...

    async def create_rerank(
        self,
        payload: Mapping[str, object],
        *,
        max_json_response_bytes: int | None = None,
    ) -> RuntimeRerankResult: ...

    def stream_chat_completion(self, payload: Mapping[str, object]) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_thaw(item) for item in value]
    return value


class LiteLLMRuntimeAdapter:
    def __init__(
        self,
        client: RuntimeClient,
        *,
        max_json_response_bytes: int | None = None,
    ) -> None:
        self._client = client
        self._max_json_response_bytes = max_json_response_bytes

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

    async def embed(
        self,
        request: ModelEmbeddingRequest,
    ) -> ModelEmbeddingResponse:
        payload = self._build_embedding_payload(request)
        response = await _call_runtime(
            lambda payload=payload: self._client.create_embedding(
                payload,
                max_json_response_bytes=self._max_json_response_bytes,
            )
        )
        del payload
        normalized = self._normalize_embedding_response(
            response,
            expected_count=len(request.inputs),
            requested_dimensions=request.dimensions,
        )
        del response
        return await _unwrap_embedding_response(normalized)

    async def rerank(
        self,
        request: ModelRerankRequest,
    ) -> ModelRerankResponse:
        payload = self._build_rerank_payload(request)
        result = await _call_runtime(
            lambda payload=payload: self._client.create_rerank(
                payload,
                max_json_response_bytes=self._max_json_response_bytes,
            )
        )
        del payload
        normalized = self._normalize_rerank_response(
            result,
            documents=request.documents,
            top_n=request.top_n,
        )
        del result
        return await _unwrap_rerank_response(normalized)

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
        parsed_stream = parse_platform_sse(stream)
        emitted_request_ids: set[str] = set()
        done = False
        try:
            while True:
                completed, event = await _next_platform_event(parsed_stream)
                if completed:
                    break
                if event.done:
                    done = True
                    yield ModelStreamEvent(event_type='done')
                    break
                normalized = self._normalize_stream_payload(event, emitted_request_ids)
                if normalized is None:
                    raise ModelServiceError.runtime_error()
                for normalized_event in normalized:
                    yield normalized_event
            if not done:
                raise ModelServiceError.runtime_error()
        finally:
            close = getattr(parsed_stream, 'aclose', None)
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
            **({'stream_options': {'include_usage': True}} if stream else {}),
        }

    @staticmethod
    def _message_payload(message: ModelMessage) -> dict[str, object]:
        return {'role': message.role, 'content': _thaw(message.content)}

    @staticmethod
    def _build_embedding_payload(request: ModelEmbeddingRequest) -> dict[str, object]:
        payload: dict[str, object] = {
            'model': request.engine_model,
            'input': list(request.inputs),
        }
        if request.dimensions is not None:
            payload['dimensions'] = request.dimensions
        if request.user is not None:
            payload['user'] = request.user
        return payload

    @staticmethod
    def _build_rerank_payload(request: ModelRerankRequest) -> dict[str, object]:
        payload: dict[str, object] = {
            'model': request.engine_model,
            'query': request.query,
            'documents': [{'text': document.text} for document in request.documents],
            'return_documents': request.return_documents,
        }
        if request.top_n is not None:
            payload['top_n'] = request.top_n
        return payload

    @staticmethod
    def _normalize_embedding_response(
        response: Mapping[str, object],
        *,
        expected_count: int,
        requested_dimensions: int | None,
    ) -> ModelEmbeddingResponse | None:
        data = response.get('data')
        if not isinstance(data, list) or len(data) != expected_count:
            return None
        vectors: list[ModelEmbeddingVector] = []
        seen_indexes: set[int] = set()
        dimension: int | None = None
        for item in data:
            vector = LiteLLMRuntimeAdapter._normalize_embedding_vector(item, seen_indexes)
            if vector is None:
                return None
            if dimension is None:
                dimension = len(vector.values)
            elif len(vector.values) != dimension:
                return None
            vectors.append(vector)
        if sorted(seen_indexes) != list(range(expected_count)):
            return None
        if requested_dimensions is not None and dimension != requested_dimensions:
            return None

        usage = response.get('usage')
        input_tokens: int | None = None
        if usage is not None:
            if not isinstance(usage, Mapping):
                return None
            token_value = usage.get('prompt_tokens', usage.get('total_tokens', 0))
            if type(token_value) is not int or token_value < 0:
                return None
            input_tokens = token_value
        valid_cost, raw_cost = LiteLLMRuntimeAdapter._optional_cost(response.get('cost', response.get('response_cost')))
        if not valid_cost:
            return None
        return ModelEmbeddingResponse(vectors=tuple(vectors), input_tokens=input_tokens, raw_cost=raw_cost)

    @staticmethod
    def _normalize_embedding_vector(
        item: object,
        seen_indexes: set[int],
    ) -> ModelEmbeddingVector | None:
        if not isinstance(item, Mapping):
            return None
        index = item.get('index')
        if type(index) is not int or index < 0 or index in seen_indexes:
            return None
        values = item.get('embedding')
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
            return None
        normalized_values: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            float_value = float(value)
            if not isfinite(float_value):
                return None
            normalized_values.append(float_value)
        if not normalized_values:
            return None
        seen_indexes.add(index)
        return ModelEmbeddingVector(index=index, values=tuple(normalized_values))

    @staticmethod
    def _normalize_rerank_response(
        result: RuntimeRerankResult,
        *,
        documents: Sequence[ModelRerankDocument],
        top_n: int | None,
    ) -> ModelRerankResponse | None:
        response = result.payload
        raw_results = response.get('results')
        if not isinstance(raw_results, list) or not raw_results:
            return None
        document_count = len(documents)
        parsed: list[tuple[int, float]] = []
        seen_indexes: set[int] = set()
        for item in raw_results:
            parsed_item = LiteLLMRuntimeAdapter._normalize_rerank_result(item, document_count, seen_indexes)
            if parsed_item is None:
                return None
            parsed.append(parsed_item)
        # 稳定排序：分数降序、相同分数按原索引升序。
        parsed.sort(key=lambda entry: (-entry[1], entry[0]))
        if top_n is not None:
            parsed = parsed[:top_n]
        results = tuple(ModelRerankResult(index=index, relevance_score=score) for index, score in parsed)

        valid_usage, input_tokens, search_units = LiteLLMRuntimeAdapter._normalize_rerank_usage(response.get('meta'))
        if not valid_usage:
            return None

        request_id_value = result.request_id or response.get('id')
        if not isinstance(request_id_value, str) or not request_id_value:
            request_id_value = f'rrk_{uuid4().hex}'

        valid_cost, raw_cost = LiteLLMRuntimeAdapter._optional_cost(result.response_cost)
        if not valid_cost:
            return None
        return ModelRerankResponse(
            request_id=request_id_value,
            results=results,
            input_tokens=input_tokens,
            search_units=search_units,
            raw_cost=raw_cost,
        )

    @staticmethod
    def _normalize_rerank_result(
        item: object,
        document_count: int,
        seen_indexes: set[int],
    ) -> tuple[int, float] | None:
        if not isinstance(item, Mapping):
            return None
        index = item.get('index')
        if type(index) is not int or not 0 <= index < document_count or index in seen_indexes:
            return None
        score = item.get('relevance_score')
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return None
        normalized_score = float(score)
        if not isfinite(normalized_score):
            return None
        seen_indexes.add(index)
        return index, normalized_score

    @staticmethod
    def _normalize_rerank_usage(meta: object) -> tuple[bool, int | None, int | None]:
        if meta is None:
            return True, None, None
        if not isinstance(meta, Mapping):
            return False, None, None
        tokens = meta.get('tokens')
        billed_units = meta.get('billed_units')
        if tokens is not None and not isinstance(tokens, Mapping):
            return False, None, None
        if billed_units is not None and not isinstance(billed_units, Mapping):
            return False, None, None
        input_tokens = tokens.get('input_tokens') if isinstance(tokens, Mapping) else None
        search_units = billed_units.get('search_units') if isinstance(billed_units, Mapping) else None
        if input_tokens is not None and (type(input_tokens) is not int or input_tokens < 0):
            return False, None, None
        if search_units is not None and (type(search_units) is not int or search_units < 1):
            return False, None, None
        return True, input_tokens, search_units

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
    def _normalize_usage(response: Mapping[str, object], *, response_cost: object) -> ModelUsage | None:
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
    def _normalize_stream_payload(
        event: PlatformSSEEvent,
        emitted_request_ids: set[str],
    ) -> tuple[ModelStreamEvent, ...] | None:
        payload = event.payload
        if payload is None:
            return None
        normalized: list[ModelStreamEvent] = []
        request_id = payload.get('id')
        if isinstance(request_id, str) and request_id and request_id not in emitted_request_ids:
            emitted_request_ids.add(request_id)
            normalized.append(ModelStreamEvent(event_type='meta', request_id=request_id))

        choices = payload.get('choices')
        if (
            isinstance(choices, Sequence)
            and not isinstance(choices, (str, bytes, bytearray))
            and choices
            and isinstance(choices[0], Mapping)
        ):
            delta = choices[0].get('delta')
            if isinstance(delta, Mapping):
                reasoning = delta.get('reasoning_content')
                if not isinstance(reasoning, str) or not reasoning:
                    reasoning = delta.get('reasoning')
                if isinstance(reasoning, str) and reasoning:
                    normalized.append(ModelStreamEvent(event_type='reasoning', delta=reasoning))
                content = delta.get('content')
                if isinstance(content, str) and content:
                    normalized.append(ModelStreamEvent(event_type='content', delta=content))

        if 'usage' in payload:
            usage_value = payload.get('usage')
            if not isinstance(usage_value, Mapping):
                return None
            response_cost = usage_value.get(
                'cost',
                payload.get('cost', payload.get('response_cost')),
            )
            usage = LiteLLMRuntimeAdapter._normalize_usage(payload, response_cost=response_cost)
            if usage is None:
                return None
            normalized.append(ModelStreamEvent(event_type='usage', usage=usage))
        return tuple(normalized)

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


async def _unwrap_embedding_response(response: ModelEmbeddingResponse | None) -> ModelEmbeddingResponse:
    if response is None:
        raise ModelServiceError.runtime_error()
    return response


async def _unwrap_rerank_response(response: ModelRerankResponse | None) -> ModelRerankResponse:
    if response is None:
        raise ModelServiceError.runtime_error()
    return response


async def _unwrap_model_ids(model_ids: set[str] | None) -> set[str]:
    if model_ids is None:
        raise ModelServiceError.runtime_error()
    return model_ids


async def _read_platform_event(stream: AsyncIterator[PlatformSSEEvent]) -> tuple[bool, PlatformSSEEvent | None]:
    try:
        return False, await anext(stream)
    except StopAsyncIteration:
        return True, None


async def _next_platform_event(
    stream: AsyncIterator[PlatformSSEEvent],
) -> tuple[bool, PlatformSSEEvent]:
    parser_failed = False
    runtime_error_factory: Callable[[], ModelServiceError] | None = None
    try:
        completed, event = await _read_platform_event(stream)
    except PlatformSSEError:
        parser_failed = True
    except LiteLLMError as error:
        runtime_error_factory = _error_factory(error)
    if runtime_error_factory is not None:
        await call_with_model_service_error(_raise_runtime_failure, runtime_error_factory)
        raise AssertionError('不可达')
    if parser_failed:
        raise ModelServiceError.runtime_error()
    if completed:
        return True, PlatformSSEEvent(event='message', payload=None, done=True)
    if event is None:
        raise AssertionError('不可达')
    return False, event
