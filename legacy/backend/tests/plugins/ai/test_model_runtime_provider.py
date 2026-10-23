from collections.abc import AsyncIterator, Mapping

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from plugins.ai.domain.model_runtime import ModelChatRequest, ModelMessage, ModelStreamEvent
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.hooks import CLIENT_STATE_NAME
from plugins.ai.infrastructure.litellm_client import RuntimeChatResult
from plugins.ai.infrastructure.model_runtime_provider import (
    MissingModelRuntimeEngine,
    get_model_runtime_engine,
)


def build_request(client: object | None = None) -> Request:
    app = FastAPI()
    if client is not None:
        setattr(app.state, CLIENT_STATE_NAME, client)
    return Request({'type': 'http', 'method': 'GET', 'path': '/ai/models', 'headers': [], 'app': app})


class RecordingByteStream:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = iter(chunks)
        self.close_count = 0

    def __aiter__(self) -> 'RecordingByteStream':
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.close_count += 1


class RecordingRuntimeClient:
    def __init__(self) -> None:
        self.close_count = 0
        self.chat_payloads: list[Mapping[str, object]] = []
        self.stream_payloads: list[Mapping[str, object]] = []
        self.streams: list[RecordingByteStream] = []

    async def list_models(self) -> Mapping[str, object]:
        return {'data': [{'id': 'company-chat'}]}

    async def create_chat_completion(self, payload: Mapping[str, object]) -> RuntimeChatResult:
        self.chat_payloads.append(payload)
        return RuntimeChatResult(payload={'choices': [{'message': {'content': 'hello'}}]})

    def stream_chat_completion(self, payload: Mapping[str, object]) -> AsyncIterator[bytes]:
        self.stream_payloads.append(payload)
        stream = RecordingByteStream(
            (
                b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n',
                b'data: [DONE]\n\n',
            )
        )
        self.streams.append(stream)
        return stream

    async def aclose(self) -> None:
        self.close_count += 1


@pytest.mark.asyncio
async def test_missing_runtime_engine_raises_stable_misconfigured_error() -> None:
    runtime = get_model_runtime_engine(build_request())

    assert isinstance(runtime, MissingModelRuntimeEngine)
    try:
        await runtime.list_model_ids()
    except ModelServiceError as error:
        assert error.code == 'MODEL_RUNTIME_MISCONFIGURED'
    else:
        raise AssertionError('缺失 Runtime 必须返回稳定配置错误')


@pytest.mark.asyncio
async def test_runtime_provider_does_not_transfer_shared_client_ownership() -> None:
    client = RecordingRuntimeClient()

    runtime = get_model_runtime_engine(build_request(client))
    await runtime.aclose()

    assert client.close_count == 0


@pytest.mark.asyncio
async def test_runtime_provider_delegates_list_and_chat_to_shared_client() -> None:
    client = RecordingRuntimeClient()
    runtime = get_model_runtime_engine(build_request(client))
    chat_request = ModelChatRequest(
        engine_model='company-chat',
        messages=(ModelMessage(role='user', content='hi'),),
        parameters={'temperature': 0.2},
    )

    model_ids = await runtime.list_model_ids()
    response = await runtime.chat(chat_request)

    assert model_ids == {'company-chat'}
    assert response.text == 'hello'
    assert client.chat_payloads == [
        {
            'model': 'company-chat',
            'messages': [{'role': 'user', 'content': 'hi'}],
            'temperature': 0.2,
            'stream': False,
        }
    ]


@pytest.mark.asyncio
async def test_runtime_provider_preserves_single_stream_close_semantics() -> None:
    client = RecordingRuntimeClient()
    runtime = get_model_runtime_engine(build_request(client))
    chat_request = ModelChatRequest(
        engine_model='company-chat',
        messages=(ModelMessage(role='user', content='hi'),),
    )

    events = [event async for event in runtime.stream_chat(chat_request)]

    assert events == [
        ModelStreamEvent(event_type='content', delta='hello'),
        ModelStreamEvent(event_type='done'),
    ]
    assert client.stream_payloads == [
        {
            'model': 'company-chat',
            'messages': [{'role': 'user', 'content': 'hi'}],
            'stream': True,
            'stream_options': {'include_usage': True},
        }
    ]
    assert len(client.streams) == 1
    assert client.streams[0].close_count == 1
