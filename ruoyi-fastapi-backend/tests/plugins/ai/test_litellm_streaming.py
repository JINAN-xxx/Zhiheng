import json
from collections.abc import AsyncIterator, Sequence

import httpx
import pytest

from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
    LiteLLMTimeoutError,
    LiteLLMUnavailableError,
)


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

    async def __aiter__(self) -> AsyncIterator[bytes]:
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
    upstream = RecordingStream([], read_error=httpx.ReadTimeout('read timed out'))

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=upstream)

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(LiteLLMTimeoutError):
            await anext(stream)
    finally:
        await client.aclose()

    assert upstream.closed is True


@pytest.mark.asyncio
async def test_stream_connect_error_is_mapped() -> None:
    """流建立前的连接故障映射为 Runtime 不可用。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('connection failed', request=request)

    client = build_stream_client(handler)
    stream = client.stream_chat_completion({'model': 'chat-alias', 'messages': []})
    try:
        with pytest.raises(LiteLLMUnavailableError):
            await anext(stream)
    finally:
        await client.aclose()
