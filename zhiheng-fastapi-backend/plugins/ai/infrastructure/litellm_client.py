import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TypeAlias

import anyio
import httpx

from middlewares.trace_middleware.ctx import TraceCtx
from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
    LiteLLMError,
    LiteLLMMisconfiguredError,
    LiteLLMProviderAuthenticationError,
    LiteLLMProviderRateLimitError,
    LiteLLMTimeoutError,
    LiteLLMUnavailableError,
    LiteLLMUpstreamError,
)

_PROVIDER_AUTH_CATEGORIES = frozenset(
    {
        'authenticationerror',
        'authentication_error',
        'invalid_api_key',
    }
)
_PROVIDER_RATE_LIMIT_CATEGORIES = frozenset(
    {
        'ratelimiterror',
        'rate_limit_error',
        'rate_limit_exceeded',
    }
)
HTTP_TOO_MANY_REQUESTS = 429
MAX_ERROR_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class RuntimeChatResult:
    payload: Mapping[str, object]
    response_cost: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class RuntimeRerankResult:
    payload: Mapping[str, object]
    response_cost: str | None = None
    request_id: str | None = None


LiteLLMFailure: TypeAlias = type[LiteLLMError]


@dataclass(frozen=True)
class _RequestOutcome:
    failure: LiteLLMFailure | None
    payload: Mapping[str, object] | None = None
    response_cost: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class _StreamOutcome:
    chunk: bytes | None = None
    failure: LiteLLMFailure | None = None


class LiteLLMRuntimeClient:
    """Platform 调用 LiteLLM 数据面的异步 HTTP 客户端。"""

    USER_AGENT = 'ZhiHeng-AI-Platform/0.1'

    def __init__(
        self,
        settings: LiteLLMSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(
                settings.request_timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
            verify=settings.verify_ssl,
            transport=transport,
        )

    async def check_liveliness(self) -> Mapping[str, object]:
        """检查 LiteLLM Proxy 进程是否存活。"""
        return await self._request_json('GET', '/health/liveliness', authenticated=False, allow_string=True)

    async def list_models(self) -> Mapping[str, object]:
        """获取当前 Runtime Key 可访问的模型。"""
        return await self._request_json('GET', '/v1/models')

    async def create_chat_completion(self, payload: Mapping[str, object]) -> RuntimeChatResult:
        """调用 OpenAI 兼容 Chat Completions 接口。"""
        operation = self._perform_json_request('POST', '/v1/chat/completions', payload=payload)
        del payload
        outcome = await operation
        result = await _unwrap_request_outcome(outcome)
        return RuntimeChatResult(
            payload=result,
            response_cost=outcome.response_cost,
            request_id=outcome.request_id,
        )

    async def create_embedding(
        self,
        payload: Mapping[str, object],
        *,
        max_json_response_bytes: int | None = None,
    ) -> Mapping[str, object]:
        """调用 OpenAI 兼容 Embeddings 接口。"""
        operation = self._request_json(
            'POST',
            '/v1/embeddings',
            payload=payload,
            max_json_response_bytes=max_json_response_bytes,
        )
        del payload
        return await operation

    async def create_rerank(
        self,
        payload: Mapping[str, object],
        *,
        max_json_response_bytes: int | None = None,
    ) -> RuntimeRerankResult:
        """调用 LiteLLM 兼容 Rerank 接口并保留成本与请求 ID。"""
        operation = self._perform_json_request(
            'POST',
            '/v1/rerank',
            payload=payload,
            max_json_response_bytes=max_json_response_bytes,
        )
        del payload
        outcome = await operation
        result = await _unwrap_request_outcome(outcome)
        return RuntimeRerankResult(
            payload=result,
            response_cost=outcome.response_cost,
            request_id=outcome.request_id,
        )

    async def stream_chat_completion(self, payload: Mapping[str, object]) -> AsyncIterator[bytes]:
        """调用 Chat Completions 并原样转发上游 SSE 字节。"""
        stream = self._stream_outcomes(payload)
        del payload
        failure: LiteLLMFailure | None = None
        try:
            async for outcome in stream:
                if outcome.failure is not None:
                    failure = outcome.failure
                    break
                if outcome.chunk is not None:
                    yield outcome.chunk
        finally:
            await stream.aclose()
        if failure is not None:
            await _raise_litellm_failure(failure)

    async def aclose(self) -> None:
        """关闭当前 Worker 的 HTTP 连接池。"""
        await self._client.aclose()

    def _build_headers(self, *, authenticated: bool) -> dict[str, str]:
        headers = {'User-Agent': self.USER_AGENT}
        request_id = TraceCtx.get_request_id()
        trace_id = TraceCtx.get_trace_id()
        if request_id:
            headers['request-id'] = request_id
        if trace_id:
            headers['trace-id'] = trace_id
        if authenticated:
            runtime_key = self.settings.runtime_key.get_secret_value().strip()
            if not runtime_key:
                raise LiteLLMMisconfiguredError
            headers['Authorization'] = f'Bearer {runtime_key}'
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        authenticated: bool = True,
        allow_string: bool = False,
        max_json_response_bytes: int | None = None,
    ) -> Mapping[str, object]:
        outcome = await self._perform_json_request(
            method,
            path,
            payload=payload,
            authenticated=authenticated,
            allow_string=allow_string,
            max_json_response_bytes=max_json_response_bytes,
        )
        del payload
        return await _unwrap_request_outcome(outcome)

    async def _perform_json_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        authenticated: bool = True,
        allow_string: bool = False,
        max_json_response_bytes: int | None = None,
    ) -> _RequestOutcome:
        try:
            async with self._client.stream(
                method,
                path,
                json=dict(payload) if payload is not None else None,
                headers=self._build_headers(authenticated=authenticated),
            ) as response:
                if not response.is_success:
                    return _RequestOutcome(failure=await self._failure_for_error_response(response))
                await response.aread()
                if max_json_response_bytes is not None and len(response.content) > max_json_response_bytes:
                    return _RequestOutcome(failure=LiteLLMUpstreamError)
                decoded = self._decode_json_mapping(response.content, allow_string=allow_string)
                if decoded is None:
                    return _RequestOutcome(failure=LiteLLMUpstreamError)
                return _RequestOutcome(
                    failure=None,
                    payload=decoded,
                    response_cost=response.headers.get('x-litellm-response-cost'),
                    request_id=(
                        response.headers.get('x-litellm-call-id')
                        or response.headers.get('x-litellm-request-id')
                        or response.headers.get('request-id')
                    ),
                )
        except httpx.TimeoutException:
            return _RequestOutcome(failure=LiteLLMTimeoutError)
        except httpx.RequestError:
            return _RequestOutcome(failure=LiteLLMUnavailableError)
        except LiteLLMError as error:
            return _RequestOutcome(failure=type(error))

    @staticmethod
    def _decode_json_mapping(body: bytes, *, allow_string: bool) -> Mapping[str, object] | None:
        try:
            result = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if allow_string and isinstance(result, str):
            return {'status': result}
        if not isinstance(result, Mapping):
            return None
        return result

    @staticmethod
    async def _failure_for_error_response(response: httpx.Response) -> LiteLLMFailure:
        if response.status_code in {401, 403}:
            return LiteLLMAuthenticationError
        if response.status_code == HTTP_TOO_MANY_REQUESTS:
            return LiteLLMProviderRateLimitError

        invalid_body, body = await LiteLLMRuntimeClient._read_bounded_error_body(response)
        if invalid_body:
            return LiteLLMUpstreamError
        categories = LiteLLMRuntimeClient._provider_error_categories_from_bytes(body)
        if categories.intersection(_PROVIDER_AUTH_CATEGORIES):
            return LiteLLMProviderAuthenticationError
        if categories.intersection(_PROVIDER_RATE_LIMIT_CATEGORIES):
            return LiteLLMProviderRateLimitError
        return LiteLLMUpstreamError

    @staticmethod
    async def _read_bounded_error_body(response: httpx.Response) -> tuple[bool, bytes]:
        content_encoding = response.headers.get('content-encoding', '').strip().casefold()
        if content_encoding not in {'', 'identity'}:
            return True, b''

        content_length = response.headers.get('content-length')
        if content_length is not None:
            invalid_content_length = False
            try:
                content_length_value = int(content_length)
            except ValueError:
                invalid_content_length = True
                content_length_value = 0
            if invalid_content_length or content_length_value < 0 or content_length_value > MAX_ERROR_BODY_BYTES:
                return True, b''

        if response.is_stream_consumed:
            body = response.content
            if len(body) > MAX_ERROR_BODY_BYTES:
                return True, b''
            return False, body

        streamed_body = bytearray()
        async for chunk in response.aiter_raw():
            if len(streamed_body) + len(chunk) > MAX_ERROR_BODY_BYTES:
                return True, b''
            streamed_body.extend(chunk)
        return False, bytes(streamed_body)

    async def _stream_outcomes(self, payload: Mapping[str, object]) -> AsyncIterator[_StreamOutcome]:
        request_payload = dict(payload)
        request_payload['stream'] = True
        failure: LiteLLMFailure | None = None
        response: httpx.Response | None = None
        interrupted = False
        try:
            request = self._client.build_request(
                'POST',
                '/v1/chat/completions',
                json=request_payload,
                headers=self._build_headers(authenticated=True),
            )
            response = await self._client.send(request, stream=True)
            if response.is_success:
                async for chunk in response.aiter_raw():
                    yield _StreamOutcome(chunk=chunk)
            else:
                failure = await self._failure_for_error_response(response)
        except (GeneratorExit, asyncio.CancelledError):
            interrupted = True
            raise
        except httpx.TimeoutException:
            failure = LiteLLMTimeoutError
        except httpx.RequestError:
            failure = LiteLLMUnavailableError
        except LiteLLMError as error:
            failure = type(error)
        finally:
            # Disconnect cancellation must not interrupt releasing the upstream
            # response, whose generator unwinds before the chat service cleanup.
            with anyio.CancelScope(shield=True):
                close_failure = await self._close_stream_response(response)
            if not interrupted and failure is None:
                failure = close_failure
        if failure is not None:
            yield _StreamOutcome(failure=failure)

    @staticmethod
    async def _close_stream_response(response: httpx.Response | None) -> LiteLLMFailure | None:
        if response is None:
            return None
        try:
            await response.aclose()
        except httpx.TimeoutException:
            return LiteLLMTimeoutError
        except httpx.RequestError:
            return LiteLLMUnavailableError
        return None

    @staticmethod
    def _provider_error_categories_from_bytes(body: bytes) -> frozenset[str]:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return frozenset()
        return LiteLLMRuntimeClient._provider_error_categories_from_payload(payload)

    @staticmethod
    def _provider_error_categories_from_payload(payload: object) -> frozenset[str]:
        if not isinstance(payload, Mapping):
            return frozenset()
        error = payload.get('error')
        if not isinstance(error, Mapping):
            return frozenset()
        return frozenset(
            value.casefold() for field in ('type', 'code') if isinstance((value := error.get(field)), str) and value
        )


async def _raise_litellm_failure(failure: LiteLLMFailure) -> None:
    raise failure


async def _unwrap_request_outcome(outcome: _RequestOutcome) -> Mapping[str, object]:
    if outcome.failure is not None:
        await _raise_litellm_failure(outcome.failure)
    if outcome.payload is None:
        raise AssertionError('不可达')
    return outcome.payload
