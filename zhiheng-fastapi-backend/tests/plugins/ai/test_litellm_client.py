import inspect
import json
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

import httpx
import pytest

from middlewares.trace_middleware.ctx import TraceCtx
from plugins.ai.config import LiteLLMSettings
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

Handler = Callable[[httpx.Request], Awaitable[httpx.Response]]
SENSITIVE_DETAIL = 'Authorization: Bearer runtime-secret at https://proxy.internal private-body'


def build_client(handler: Handler, *, runtime_key: str = 'runtime-secret') -> LiteLLMRuntimeClient:
    """使用 MockTransport 创建隔离的 Runtime 客户端。"""
    settings = LiteLLMSettings(
        LITELLM_BASE_URL='https://proxy.example.com',
        LITELLM_RUNTIME_KEY=runtime_key,
    )
    return LiteLLMRuntimeClient(settings, transport=httpx.MockTransport(handler))


def assert_sanitized_internal_error(error: Exception) -> None:
    serialized = repr(
        (
            str(error),
            repr(error),
            vars(error),
            error.args,
            error.__cause__,
            error.__context__,
            ''.join(traceback.format_exception(error)),
        )
    ).lower()
    assert error.__cause__ is None
    assert error.__context__ is None
    assert 'authorization' not in serialized
    assert 'runtime-secret' not in serialized
    assert 'proxy.internal' not in serialized
    assert 'private-body' not in serialized
    current = error.__traceback__
    while current is not None:
        filename = current.tb_frame.f_code.co_filename.replace('\\', '/')
        if '/plugins/ai/' in filename and '/tests/' not in filename:
            for value in current.tb_frame.f_locals.values():
                assert not isinstance(value, (httpx.Request, httpx.Response))
                local_repr = repr(value).lower()
                assert 'authorization' not in local_repr
                assert 'runtime-secret' not in local_repr
                assert 'proxy.internal' not in local_repr
                assert 'provider-secret' not in local_repr
                assert 'provider.internal' not in local_repr
                assert 'private-body' not in local_repr
        current = current.tb_next


class RecordingResponseStream(httpx.AsyncByteStream):
    def __init__(
        self,
        chunks: Sequence[bytes],
        *,
        read_error: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.read_error = read_error
        self.iteration_count = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.iteration_count += 1
            yield chunk
        if self.read_error is not None:
            raise self.read_error

    async def aclose(self) -> None:
        self.closed = True


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
        assert request.headers['user-agent'] == 'ZhiHeng-AI-Platform/0.1'
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
        return httpx.Response(
            200,
            json={'id': 'response-1', 'object': 'result'},
            headers={
                'x-litellm-response-cost': '0.00125',
                'x-litellm-call-id': 'runtime-request-1',
                'authorization': 'Bearer response-secret',
                'x-private-header': 'must-not-survive',
            },
        )

    client = build_client(payload_handler)
    method = getattr(client, method_name)
    try:
        result = await method(payload)
    finally:
        await client.aclose()

    if method_name == 'create_chat_completion':
        assert result == RuntimeChatResult(
            payload={'id': 'response-1', 'object': 'result'},
            response_cost='0.00125',
            request_id='runtime-request-1',
        )
        assert 'response-secret' not in repr(result)
        assert 'must-not-survive' not in repr(result)
    else:
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
async def test_liveliness_normalizes_litellm_json_string_response() -> None:
    """LiteLLM v1.95 的 JSON 字符串存活响应被规范化为内部对象。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json="I'm alive!")

    client = build_client(handler, runtime_key='')
    try:
        assert await client.check_liveliness() == {'status': "I'm alive!"}
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
        (429, LiteLLMProviderRateLimitError),
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
    ('status_code', 'error_field', 'error_value', 'error_type'),
    (
        (400, 'type', 'AuthenticationError', LiteLLMProviderAuthenticationError),
        (500, 'type', 'authentication_error', LiteLLMProviderAuthenticationError),
        (400, 'code', 'invalid_api_key', LiteLLMProviderAuthenticationError),
        (500, 'type', 'RateLimitError', LiteLLMProviderRateLimitError),
        (500, 'type', 'rate_limit_error', LiteLLMProviderRateLimitError),
        (500, 'code', 'rate_limit_exceeded', LiteLLMProviderRateLimitError),
    ),
)
async def test_structured_provider_error_category_is_mapped_without_retaining_body(
    status_code: int,
    error_field: str,
    error_value: str,
    error_type: type[Exception],
) -> None:
    secret = 'provider-secret-at-https://provider.internal'

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={'error': {error_field: error_value, 'message': secret, 'provider': 'private-provider'}},
        )

    client = build_client(handler)
    try:
        with pytest.raises(error_type) as exc_info:
            await client.create_chat_completion({'model': 'company-chat', 'messages': []})
    finally:
        await client.aclose()

    error = exc_info.value
    serialized = repr(
        (
            str(error),
            repr(error),
            vars(error),
            error.args,
            error.__cause__,
            error.__context__,
            ''.join(traceback.format_exception(error)),
        )
    )
    assert secret not in serialized
    assert 'private-provider' not in serialized
    assert_sanitized_internal_error(error)


@pytest.mark.asyncio
async def test_runtime_auth_status_takes_precedence_over_provider_error_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={'error': {'type': 'RateLimitError', 'message': 'provider-secret'}},
        )

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMAuthenticationError):
            await client.list_models()
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_known_provider_code_is_used_when_error_type_is_unknown() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={'error': {'type': 'UnknownProviderFailure', 'code': 'rate_limit_exceeded'}},
        )

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMProviderRateLimitError):
            await client.create_chat_completion({'model': 'company-chat', 'messages': []})
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    (
        {'error': {'type': 'UnknownProviderFailure', 'message': 'provider-secret'}},
        {'error': 'not-an-object'},
        ['not-an-object'],
    ),
)
async def test_unrecognized_provider_error_stays_sanitized_generic_upstream_error(body: object) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json=body)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMUpstreamError) as exc_info:
            await client.create_chat_completion({'model': 'company-chat', 'messages': []})
    finally:
        await client.aclose()

    assert 'provider-secret' not in repr((str(exc_info.value), vars(exc_info.value), exc_info.value.args))


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
            raise httpx.ReadTimeout(SENSITIVE_DETAIL, request=request)
        raise httpx.ConnectError(SENSITIVE_DETAIL, request=request)

    client = build_client(handler)
    try:
        with pytest.raises(error_type) as raised:
            await client.list_models()
    finally:
        await client.aclose()

    assert_sanitized_internal_error(raised.value)


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


@pytest.mark.asyncio
async def test_invalid_json_error_does_not_retain_raw_response_or_decode_context() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=SENSITIVE_DETAIL)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMUpstreamError) as raised:
            await client.list_models()
    finally:
        await client.aclose()

    assert_sanitized_internal_error(raised.value)


@pytest.mark.asyncio
async def test_success_response_is_fully_read_and_closed_before_return() -> None:
    upstream = RecordingResponseStream([b'{"data":', b'[{"id":"company-chat"}]}'])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=upstream)

    client = build_client(handler)
    try:
        assert await client.list_models() == {'data': [{'id': 'company-chat'}]}
    finally:
        await client.aclose()

    assert upstream.iteration_count == len(upstream.chunks)
    assert upstream.closed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('headers', 'expected_iterations'),
    (
        ({'content-length': str(64 * 1024 + 1)}, 0),
        ({'content-length': '-1'}, 0),
        ({'content-length': SENSITIVE_DETAIL}, 0),
        ({'content-encoding': 'gzip'}, 0),
    ),
)
async def test_non_stream_error_rejects_unsafe_body_without_reading(
    headers: dict[str, str],
    expected_iterations: int,
) -> None:
    upstream = RecordingResponseStream([SENSITIVE_DETAIL.encode()])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=upstream, headers=headers)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMUpstreamError) as raised:
            await client.list_models()
    finally:
        await client.aclose()

    assert upstream.iteration_count == expected_iterations
    assert upstream.closed is True
    assert_sanitized_internal_error(raised.value)


@pytest.mark.asyncio
async def test_non_stream_error_stops_reading_when_chunk_limit_is_exceeded() -> None:
    upstream = RecordingResponseStream([b'x' * (32 * 1024), b'y' * (32 * 1024 + 1), b'must-not-read'])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=upstream)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMUpstreamError):
            await client.list_models()
    finally:
        await client.aclose()

    assert upstream.iteration_count == len(upstream.chunks) - 1
    assert upstream.closed is True


@pytest.mark.asyncio
async def test_non_stream_small_structured_error_is_still_classified_and_closed() -> None:
    upstream = RecordingResponseStream(
        [json.dumps({'error': {'type': 'AuthenticationError', 'message': SENSITIVE_DETAIL}}).encode()]
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=upstream, headers={'content-type': 'application/json'})

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMProviderAuthenticationError) as raised:
            await client.create_chat_completion({'model': 'company-chat', 'messages': []})
    finally:
        await client.aclose()

    assert upstream.iteration_count == 1
    assert upstream.closed is True
    assert_sanitized_internal_error(raised.value)


@pytest.mark.asyncio
async def test_non_stream_success_read_timeout_is_sanitized_and_response_is_closed() -> None:
    upstream = RecordingResponseStream([], read_error=httpx.ReadTimeout(SENSITIVE_DETAIL))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=upstream)

    client = build_client(handler)
    try:
        with pytest.raises(LiteLLMTimeoutError) as raised:
            await client.list_models()
    finally:
        await client.aclose()

    assert upstream.closed is True
    assert_sanitized_internal_error(raised.value)
