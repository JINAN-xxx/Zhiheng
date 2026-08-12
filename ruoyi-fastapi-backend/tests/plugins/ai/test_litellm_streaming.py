import json
import traceback
from collections.abc import AsyncIterator, Sequence

import httpx
import pytest

from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
    LiteLLMProviderAuthenticationError,
    LiteLLMProviderRateLimitError,
    LiteLLMTimeoutError,
    LiteLLMUnavailableError,
    LiteLLMUpstreamError,
)

SENSITIVE_DETAIL = 'Authorization: Bearer runtime-secret at https://proxy.internal private-body'


class RecordingStream(httpx.AsyncByteStream):
    """记录关闭状态的测试字节流。"""

    def __init__(
        self,
        chunks: Sequence[bytes],
        *,
        read_error: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.read_error = read_error
        self.closed = False
        self.iterated = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.iterated = True
        for chunk in self.chunks:
            yield chunk
        if self.read_error is not None:
            raise self.read_error

    async def aclose(self) -> None:
        self.closed = True


def build_stream_client(handler: object) -> LiteLLMRuntimeClient:
    """使用测试 Transport 创建流式客户端。"""
    settings = LiteLLMSettings(
        LITELLM_BASE_URL='https://proxy.example.com',
        LITELLM_RUNTIME_KEY='runtime-secret',
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


@pytest.mark.asyncio
async def test_stream_chat_completion_preserves_raw_sse_bytes() -> None:
    """SSE 分片不经过解码或重新序列化。"""
    chunks = [b'data: {"id":"1"}\n\n', b'data: [DONE]\n\n']
    upstream = RecordingStream(chunks)
    payload = {'model': 'chat-alias', 'messages': []}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer runtime-secret'
        assert json.loads(request.content) == {**payload, 'stream': True}
        return httpx.Response(200, stream=upstream)

    client = build_stream_client(handler)
    try:
        result = [chunk async for chunk in client.stream_chat_completion(payload)]
    finally:
        await client.aclose()

    assert result == chunks
    assert payload == {'model': 'chat-alias', 'messages': []}
    assert upstream.closed is True


@pytest.mark.asyncio
async def test_stream_rejects_auth_error_before_first_chunk_and_closes_upstream() -> None:
    """认证失败在返回分片前转换，并关闭错误响应。"""
    upstream = RecordingStream([b'private-upstream-body'])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, stream=upstream)

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(LiteLLMAuthenticationError) as exc_info:
            await anext(stream)
    finally:
        await client.aclose()

    assert upstream.closed is True
    assert upstream.iterated is False
    assert 'private-upstream-body' not in str(exc_info.value)


@pytest.mark.asyncio
async def test_closing_downstream_generator_closes_upstream_response() -> None:
    """下游停止消费时立即释放上游连接。"""
    upstream = RecordingStream([b'data: first\n\n', b'data: second\n\n'])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=upstream)

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        assert await anext(stream) == b'data: first\n\n'
        assert upstream.closed is False
        await stream.aclose()
        assert upstream.closed is True
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_stream_read_timeout_is_mapped_and_closes_upstream() -> None:
    """流建立后的读取超时映射为网关超时并释放响应。"""
    upstream = RecordingStream([], read_error=httpx.ReadTimeout(SENSITIVE_DETAIL))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=upstream)

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(LiteLLMTimeoutError) as raised:
            await anext(stream)
    finally:
        await client.aclose()

    assert upstream.closed is True
    assert_sanitized_internal_error(raised.value)


@pytest.mark.asyncio
async def test_stream_connect_error_is_mapped() -> None:
    """流建立前的连接故障映射为 Runtime 不可用。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(SENSITIVE_DETAIL, request=request)

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(LiteLLMUnavailableError) as raised:
            await anext(stream)
    finally:
        await client.aclose()

    assert_sanitized_internal_error(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('error_type_name', 'expected_error'),
    (
        ('AuthenticationError', LiteLLMProviderAuthenticationError),
        ('RateLimitError', LiteLLMProviderRateLimitError),
    ),
)
async def test_stream_structured_provider_error_is_classified_without_leaking_body(
    error_type_name: str,
    expected_error: type[Exception],
) -> None:
    secret = 'provider-secret-at-https://provider.internal'
    body = json.dumps({'error': {'type': error_type_name, 'message': secret}}).encode()
    upstream = RecordingStream([body])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=upstream, headers={'content-type': 'application/json'})

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(expected_error) as raised:
            await anext(stream)
    finally:
        await client.aclose()

    assert upstream.closed is True
    assert secret not in repr((str(raised.value), vars(raised.value), raised.value.args))


@pytest.mark.asyncio
@pytest.mark.parametrize('use_content_length', (True, False))
async def test_stream_oversized_error_body_falls_back_to_generic_error(
    use_content_length: bool,
) -> None:
    body = b'x' * (64 * 1024 + 1)
    upstream = RecordingStream([body])

    async def handler(request: httpx.Request) -> httpx.Response:
        headers = {'content-length': str(len(body))} if use_content_length else {}
        return httpx.Response(500, stream=upstream, headers=headers)

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(LiteLLMUpstreamError):
            await anext(stream)
    finally:
        await client.aclose()

    assert upstream.closed is True
    assert upstream.iterated is not use_content_length


@pytest.mark.asyncio
async def test_stream_invalid_json_error_is_sanitized() -> None:
    upstream = RecordingStream([SENSITIVE_DETAIL.encode()])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=upstream, headers={'content-type': 'application/json'})

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(LiteLLMUpstreamError) as raised:
            await anext(stream)
    finally:
        await client.aclose()

    assert upstream.closed is True
    assert_sanitized_internal_error(raised.value)


@pytest.mark.asyncio
async def test_stream_invalid_content_length_is_sanitized() -> None:
    upstream = RecordingStream([b'not-read'])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=upstream, headers={'content-length': SENSITIVE_DETAIL})

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(LiteLLMUpstreamError) as raised:
            await anext(stream)
    finally:
        await client.aclose()

    assert upstream.iterated is False
    assert upstream.closed is True
    assert_sanitized_internal_error(raised.value)


@pytest.mark.asyncio
async def test_stream_compressed_error_is_not_decompressed_or_consumed() -> None:
    upstream = RecordingStream([b'compressed-private-body'])

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, stream=upstream, headers={'content-encoding': 'gzip'})

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(LiteLLMUpstreamError):
            await anext(stream)
    finally:
        await client.aclose()

    assert upstream.iterated is False
    assert upstream.closed is True
