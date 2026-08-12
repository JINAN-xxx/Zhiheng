from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

import httpx

from middlewares.trace_middleware.ctx import TraceCtx
from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
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


@dataclass(frozen=True)
class RuntimeChatResult:
    payload: Mapping[str, object]
    response_cost: str | None = None
    request_id: str | None = None


class LiteLLMRuntimeClient:
    """Platform 调用 LiteLLM 数据面的异步 HTTP 客户端。"""

    USER_AGENT = 'RuoYi-AI-Platform/0.1'

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
        response = await self._request('POST', '/v1/chat/completions', payload=payload)
        result = self._decode_json_object(response)
        return RuntimeChatResult(
            payload=result,
            response_cost=response.headers.get('x-litellm-response-cost'),
            request_id=(
                response.headers.get('x-litellm-call-id')
                or response.headers.get('x-litellm-request-id')
                or response.headers.get('request-id')
            ),
        )

    async def create_embedding(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        """调用 OpenAI 兼容 Embeddings 接口。"""
        return await self._request_json('POST', '/v1/embeddings', payload=payload)

    async def stream_chat_completion(self, payload: Mapping[str, object]) -> AsyncIterator[bytes]:
        """调用 Chat Completions 并原样转发上游 SSE 字节。"""
        request_payload = dict(payload)
        request_payload['stream'] = True
        try:
            async with self._client.stream(
                'POST',
                '/v1/chat/completions',
                json=request_payload,
                headers=self._build_headers(authenticated=True),
            ) as response:
                self._raise_for_status(response)
                async for chunk in response.aiter_raw():
                    yield chunk
        except httpx.TimeoutException as exc:
            raise LiteLLMTimeoutError from exc
        except httpx.RequestError as exc:
            raise LiteLLMUnavailableError from exc

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
    ) -> Mapping[str, object]:
        response = await self._request(method, path, payload=payload, authenticated=authenticated)
        try:
            result = response.json()
        except ValueError as exc:
            raise LiteLLMUpstreamError from exc
        if allow_string and isinstance(result, str):
            return {'status': result}
        if allow_string:
            if not isinstance(result, Mapping):
                raise LiteLLMUpstreamError
            return result
        return self._decode_json_object(response)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        authenticated: bool = True,
    ) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                path,
                json=dict(payload) if payload is not None else None,
                headers=self._build_headers(authenticated=authenticated),
            )
        except httpx.TimeoutException as exc:
            raise LiteLLMTimeoutError from exc
        except httpx.RequestError as exc:
            raise LiteLLMUnavailableError from exc
        self._raise_for_status(response)
        return response

    @staticmethod
    def _decode_json_object(response: httpx.Response) -> Mapping[str, object]:
        try:
            result = response.json()
        except ValueError as exc:
            raise LiteLLMUpstreamError from exc
        if not isinstance(result, Mapping):
            raise LiteLLMUpstreamError
        return result

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        if response.status_code in {401, 403}:
            raise LiteLLMAuthenticationError
        if response.status_code == HTTP_TOO_MANY_REQUESTS:
            raise LiteLLMProviderRateLimitError
        categories = LiteLLMRuntimeClient._provider_error_categories(response)
        if categories.intersection(_PROVIDER_AUTH_CATEGORIES):
            raise LiteLLMProviderAuthenticationError
        if categories.intersection(_PROVIDER_RATE_LIMIT_CATEGORIES):
            raise LiteLLMProviderRateLimitError
        raise LiteLLMUpstreamError

    @staticmethod
    def _provider_error_categories(response: httpx.Response) -> frozenset[str]:
        try:
            payload = response.json()
        except ValueError:
            return frozenset()
        if not isinstance(payload, Mapping):
            return frozenset()
        error = payload.get('error')
        if not isinstance(error, Mapping):
            return frozenset()
        return frozenset(
            value.casefold() for field in ('type', 'code') if isinstance((value := error.get(field)), str) and value
        )
