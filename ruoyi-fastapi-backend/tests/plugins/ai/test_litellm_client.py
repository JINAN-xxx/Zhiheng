import inspect
import json
from collections.abc import Awaitable, Callable

import httpx
import pytest

from middlewares.trace_middleware.ctx import TraceCtx
from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
    LiteLLMMisconfiguredError,
    LiteLLMTimeoutError,
    LiteLLMUnavailableError,
    LiteLLMUpstreamError,
)

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]


def build_client(handler: Handler, *, runtime_key: str = 'runtime-secret') -> LiteLLMRuntimeClient:
    """使用 MockTransport 创建隔离的 Runtime 客户端。"""
    settings = LiteLLMSettings(
        LITELLM_BASE_URL='https://proxy.example.com',
        LITELLM_RUNTIME_KEY=runtime_key,
    )
    return LiteLLMRuntimeClient(settings, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_models_adds_runtime_auth_and_trace_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型列表请求只使用内部 Runtime Key，并透传当前追踪标识。"""
    monkeypatch.setattr(TraceCtx, 'get_request_id', staticmethod(lambda: 'request-1'))
    monkeypatch.setattr(TraceCtx, 'get_trace_id', staticmethod(lambda: 'trace-1'))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == 'GET'
        assert request.url.path == '/v1/models'
        assert request.headers['authorization'] == 'Bearer runtime-secret'
        assert request.headers['request-id'] == 'request-1'
        assert request.headers['trace-id'] == 'trace-1'
        assert request.headers['user-agent'] == 'RuoYi-AI-Platform/0.1'
        return httpx.Response(200, json={'object': 'list', 'data': []})

    client = build_client(handler)

    try:
        assert await client.list_models() == {'object': 'list', 'data': []}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_empty_trace_values_are_not_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """请求上下文没有追踪标识时不发送空追踪头。"""
    monkeypatch.setattr(TraceCtx, 'get_request_id', staticmethod(lambda: ''))
    monkeypatch.setattr(TraceCtx, 'get_trace_id', staticmethod(lambda: ''))

    async def handler(request: httpx.Request) -> httpx.Response:
        assert 'request-id' not in request.headers
        assert 'trace-id' not in request.headers
        return httpx.Response(200, json={'data': []})

    client = build_client(handler)
    try:
        await client.list_models()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method_name', 'path', 'payload'),
    (
        ('create_chat_completion', '/v1/chat/completions', {'model': 'chat-alias', 'messages': []}),
        ('create_embedding', '/v1/embeddings', {'model': 'embedding-alias', 'input': 'hello'}),
    ),
)
async def test_json_methods_forward_openai_compatible_payload(
    method_name: str,
    path: str,
    payload: dict[str, object],
) -> None:
    """Chat 和 Embedding 方法原样转发 OpenAI 兼容 JSON 字段。"""
    captured_payload = payload.copy()

    async def payload_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == 'POST'
        assert request.url.path == path
        assert request.headers['authorization'] == 'Bearer runtime-secret'
        assert json.loads(request.content) == payload
        return httpx.Response(200, json={'id': 'response-1', 'object': 'result'})

    client = build_client(payload_handler)
    method = getattr(client, method_name)
    try:
        result = await method(payload)
    finally:
        await client.aclose()

    assert result == {'id': 'response-1', 'object': 'result'}
    assert payload == captured_payload


@pytest.mark.asyncio
async def test_liveliness_does_not_require_runtime_key() -> None:
    """Liveness 是公开运行状态检查，不因 Runtime Key 缺失失败。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/health/liveliness'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'status': 'healthy'})

    client = build_client(handler, runtime_key='')
    try:
        assert await client.check_liveliness() == {'status': 'healthy'}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_authenticated_calls_fail_fast_when_runtime_key_missing() -> None:
    """Runtime Key 缺失时不向网络发出认证请求。"""
    transport_called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return httpx.Response(200, json={'data': []})

    client = build_client(handler, runtime_key='  ')
    try:
        with pytest.raises(LiteLLMMisconfiguredError):
            await client.list_models()
    finally:
        await client.aclose()

    assert transport_called is False


def test_public_methods_do_not_allow_authorization_override() -> None:
    """调用方不能通过公开方法参数覆盖内部 Authorization。"""
    for method_name in ('list_models', 'create_chat_completion', 'create_embedding'):
        assert 'headers' not in inspect.signature(getattr(LiteLLMRuntimeClient, method_name)).parameters


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('upstream_status', 'error_type'),
    (
        (401, LiteLLMAuthenticationError),
        (403, LiteLLMAuthenticationError),
        (429, LiteLLMUpstreamError),
        (500, LiteLLMUpstreamError),
    ),
)
async def test_upstream_status_is_mapped_without_response_body(
    upstream_status: int,
    error_type: type[Exception],
) -> None:
    """上游状态映射为稳定异常，且不携带上游响应正文。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(upstream_status, text='private-upstream-body')

    client = build_client(handler)
    try:
        with pytest.raises(error_type) as exc_info:
            await client.list_models()
    finally:
        await client.aclose()

    assert 'private-upstream-body' not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('error_kind', 'error_type'),
    (
        ('timeout', LiteLLMTimeoutError),
        ('connect', LiteLLMUnavailableError),
    ),
)
async def test_transport_errors_have_stable_mappings(
    error_kind: str,
    error_type: type[Exception],
) -> None:
    """超时和其他网络故障映射为不同的网关异常。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        if error_kind == 'timeout':
            raise httpx.ReadTimeout('read timed out', request=request)
        raise httpx.ConnectError('connection failed', request=request)

    client = build_client(handler)
    try:
        with pytest.raises(error_type):
            await client.list_models()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_successful_non_object_json_is_rejected() -> None:
    """成功响应必须是 JSON object，避免不稳定类型穿过客户端边界。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=['unexpected'])

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMUpstreamError):
            await client.list_models()
    finally:
        await client.aclose()
