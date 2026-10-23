"""Public stream ownership must survive cancellable HTTP, Redis and close waits."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import anyio
import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope

from plugins.ai.config import LiteLLMSettings
from plugins.ai.dao.ai_developer_api_dao import InvocationSnapshotRecord
from plugins.ai.domain.model_runtime import ModelChatRequest, ModelMessage, ModelStreamEvent
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.do.ai_developer_api_do import AiApiUsageLedger
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient
from plugins.ai.infrastructure.litellm_runtime_adapter import LiteLLMRuntimeAdapter
from plugins.ai.infrastructure.openai_stream_adapter import OpenAIStreamAdapter
from plugins.ai.public.api_transport import streaming_response
from plugins.ai.service.model_service import ModelServiceStreamResult
from plugins.ai.service.public_invocation_coordinator import (
    CoordinatorStream,
    PublicInvocationCoordinator,
    PublicQuotaEngine,
)
from tests.plugins.ai.test_public_invocation_coordinator import (
    KEY_ID,
    MODEL_CODE,
    NOW,
    StubSnapshotDao,
    _identity,
    _snapshot,
)
from tests.plugins.ai.test_public_invocation_coordinator import ledger_database as _ledger_database

ledger_database = _ledger_database
SETTLEMENT_KEY_COUNT = 4


class NetworkCheckpointRedis(fakeredis.aioredis.FakeRedis):
    """Run real quota Lua while simulating the real client's cancellable I/O."""

    settlements = 0
    cancel_next_settlement = False

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        await anyio.lowlevel.checkpoint()
        if numkeys == SETTLEMENT_KEY_COUNT and self.cancel_next_settlement:
            self.cancel_next_settlement = False
            raise asyncio.CancelledError('settlement interrupted')
        result = await super().eval(script, numkeys, *args)
        if numkeys == SETTLEMENT_KEY_COUNT and result == 1:
            self.settlements += 1
        return result


class CheckpointStream:
    def __init__(self, failure: BaseException | None = None, close_failure: BaseException | None = None) -> None:
        self.failure = failure
        self.close_failure = close_failure
        self.close_count = 0

    def __aiter__(self) -> 'CheckpointStream':
        return self

    async def __anext__(self) -> ModelStreamEvent:
        await anyio.lowlevel.checkpoint()
        if self.failure is not None:
            raise self.failure
        return ModelStreamEvent(event_type='content', delta='partial answer')

    async def aclose(self) -> None:
        await anyio.lowlevel.checkpoint()
        if self.close_failure is not None:
            raise self.close_failure
        self.close_count += 1


@dataclass
class StreamEnvironment:
    sessions: async_sessionmaker[AsyncSession]
    redis: NetworkCheckpointRedis
    coordinator: PublicInvocationCoordinator
    streams: list[CoordinatorStream] = field(default_factory=list)

    async def start(self, source: AsyncIterator[ModelStreamEvent]) -> CoordinatorStream:
        async def operation(snapshot: InvocationSnapshotRecord) -> ModelServiceStreamResult:
            return ModelServiceStreamResult(
                platform_model_id=snapshot.platform_model_id,
                binding_id=snapshot.binding_id,
                engine_type=snapshot.engine_type,
                engine_model_snapshot=snapshot.engine_model_snapshot,
                _stream=source,
            )

        stream = await self.coordinator.run_stream(
            _identity(), MODEL_CODE, 'chat', operation_type='chat', input_bytes=10, operation=operation
        )
        self.streams.append(stream)
        return stream

    async def assert_settled(self, status: str = 'cancelled') -> None:
        async with self.sessions() as session:
            ledger = (await session.execute(select(AiApiUsageLedger))).scalar_one()
            assert ledger.status == status
            assert ledger.finished_at is not None
            assert ledger.cost_estimated is True
            assert ledger.billable_cost == ledger.reserved_cost
            assert await self.redis.exists(f'ai:quota:lease:{ledger.request_id}') == 0
        assert await self.redis.get(f'ai:quota:concurrency:{KEY_ID}') == b'0'
        assert self.redis.settlements == 1

    async def assert_same_key_can_call_again(self) -> None:
        source = CheckpointStream()
        stream = await self.start(source)
        assert await self.redis.get(f'ai:quota:concurrency:{KEY_ID}') == b'1'
        await stream.aclose()
        assert source.close_count == 1
        assert await self.redis.get(f'ai:quota:concurrency:{KEY_ID}') == b'0'


@pytest_asyncio.fixture
async def stream_environment(ledger_database: Any) -> AsyncIterator[StreamEnvironment]:
    _, sessions = ledger_database
    async with NetworkCheckpointRedis() as redis, sessions() as db:
        StubSnapshotDao.snapshot = _snapshot(concurrency_limit=1)
        coordinator = PublicInvocationCoordinator(
            db,
            redis,
            session_factory=sessions,
            quota=PublicQuotaEngine(redis, clock=lambda: NOW),
            dao=StubSnapshotDao,
            clock=lambda: NOW,
        )
        environment = StreamEnvironment(sessions, redis, coordinator)
        try:
            yield environment
        finally:
            for stream in environment.streams:
                await stream.aclose()


def openai_stream(stream: CoordinatorStream) -> OpenAIStreamAdapter:
    return OpenAIStreamAdapter(completion_id='chatcmpl_' + '1' * 32, created=1, model_code=MODEL_CODE, stream=stream)


@pytest.mark.asyncio
@pytest.mark.parametrize('disconnect_at', ['headers', 'role', 'content', 'waiting'])
async def test_real_public_sse_disconnect_settles_and_releases_same_key(
    stream_environment: StreamEnvironment, disconnect_at: str
) -> None:
    delivered = anyio.Event()

    class Body(httpx.AsyncByteStream):
        close_count = 0

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"choices":[{"index":0,"delta":{"content":"partial answer"},"finish_reason":null}]}\n\n'
            delivered.set()
            await anyio.sleep_forever()

        async def aclose(self) -> None:
            await anyio.lowlevel.checkpoint()
            self.close_count += 1

    body = Body()

    async def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=body)

    client = LiteLLMRuntimeClient(
        LiteLLMSettings(LITELLM_RUNTIME_KEY='test-only'), transport=httpx.MockTransport(upstream)
    )
    source = LiteLLMRuntimeAdapter(client).stream_chat(
        ModelChatRequest(
            engine_model='test-model', messages=(ModelMessage(role='user', content='hello'),), parameters={}
        )
    )
    stream = await stream_environment.start(source)
    response = streaming_response(openai_stream(stream))

    async def receive() -> Message:
        await delivered.wait()
        return {'type': 'http.disconnect'}

    async def send(message: Message) -> None:
        if (
            (disconnect_at == 'headers' and message['type'] == 'http.response.start')
            or (disconnect_at == 'role' and b'"role"' in message.get('body', b''))
            or (disconnect_at == 'content' and b'partial answer' in message.get('body', b''))
        ):
            delivered.set()
            await anyio.sleep_forever()

    scope: Scope = {
        'type': 'http',
        'asgi': {'version': '3.0', 'spec_version': '2.3'},
        'method': 'POST',
        'path': '/v1/chat/completions',
        'headers': [],
    }
    try:
        with anyio.fail_after(5):
            await response(scope, receive, send)
        await stream_environment.assert_settled()
        await response.body_iterator.aclose()
        await stream.aclose()
        await stream_environment.assert_settled()
        assert body.close_count == int(disconnect_at in {'content', 'waiting'})
        await stream_environment.assert_same_key_can_call_again()
    finally:
        await source.aclose()
        await client.aclose()


@pytest.mark.asyncio
async def test_unstarted_response_iterator_close_owns_reserved_stream(stream_environment: StreamEnvironment) -> None:
    source = CheckpointStream()
    stream = await stream_environment.start(source)
    response = streaming_response(openai_stream(stream))
    with anyio.CancelScope() as request_scope:
        request_scope.cancel()
        await response.body_iterator.aclose()
    await stream_environment.assert_settled()
    await response.body_iterator.aclose()
    await stream.aclose()
    assert source.close_count == 1
    await stream_environment.assert_settled()


@pytest.mark.asyncio
async def test_close_shields_wait_for_active_reader_lock(stream_environment: StreamEnvironment) -> None:
    reading = anyio.Event()
    finish_read = anyio.Event()
    closing = anyio.Event()

    class WaitingStream(CheckpointStream):
        async def __anext__(self) -> ModelStreamEvent:
            reading.set()
            await finish_read.wait()
            return await super().__anext__()

    source = WaitingStream()
    stream = await stream_environment.start(source)

    async def read() -> None:
        await anext(stream)

    async def close() -> None:
        with anyio.CancelScope() as request_scope:
            request_scope.cancel()
            closing.set()
            await stream.aclose()

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(read)
            await reading.wait()
            tasks.start_soon(close)
            await closing.wait()
            finish_read.set()
    await stream_environment.assert_settled()
    assert source.close_count == 1


@pytest.mark.asyncio
async def test_interrupted_settlement_can_be_retried_without_reclosing_source(
    stream_environment: StreamEnvironment,
) -> None:
    source = CheckpointStream()
    stream = await stream_environment.start(source)
    response = streaming_response(openai_stream(stream))
    stream_environment.redis.cancel_next_settlement = True
    with pytest.raises(asyncio.CancelledError, match='settlement interrupted'):
        await response.body_iterator.aclose()
    assert await stream_environment.redis.get(f'ai:quota:concurrency:{KEY_ID}') == b'1'
    await response.body_iterator.aclose()
    await response.body_iterator.aclose()
    assert source.close_count == 1
    await stream_environment.assert_settled()


@pytest.mark.asyncio
@pytest.mark.parametrize('model_error', [False, True])
async def test_response_settlement_retry_preserves_original_read_failure(
    stream_environment: StreamEnvironment, model_error: bool
) -> None:
    failure = ModelServiceError.runtime_error() if model_error else RuntimeError('read failed')
    source = CheckpointStream(failure=failure)
    stream = await stream_environment.start(source)
    response = streaming_response(openai_stream(stream))
    stream_environment.redis.cancel_next_settlement = True

    async def receive() -> Message:
        await anyio.sleep_forever()

    async def send(message: Message) -> None:
        del message

    scope: Scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}}
    with pytest.raises(type(failure)) as raised:
        await response(scope, receive, send)
    assert raised.value is failure
    await stream_environment.assert_settled('failed')
    async with stream_environment.sessions() as session:
        ledger = (await session.execute(select(AiApiUsageLedger))).scalar_one()
        assert ledger.error_code == ('MODEL_RUNTIME_ERROR' if model_error else 'INTERNAL')
    await response.body_iterator.aclose()
    await stream.aclose()
    assert source.close_count == 1
    await stream_environment.assert_settled('failed')


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_response_settlement_retry_preserves_effective_close_failure(
    stream_environment: StreamEnvironment, cancelled: bool
) -> None:
    class DoneStream(CheckpointStream):
        async def __anext__(self) -> ModelStreamEvent:
            return ModelStreamEvent(event_type='done')

        async def aclose(self) -> None:
            close_failure = self.close_failure
            self.close_failure = None
            if close_failure is not None:
                raise close_failure
            await super().aclose()

    close_failure = asyncio.CancelledError('close cancelled') if cancelled else RuntimeError('close failed')
    source = DoneStream(close_failure=close_failure)
    stream = await stream_environment.start(source)
    response = streaming_response(openai_stream(stream))
    assert b'"role"' in await anext(response.body_iterator)
    stream_environment.redis.cancel_next_settlement = True
    with pytest.raises(asyncio.CancelledError, match='settlement interrupted'):
        await anext(response.body_iterator)
    expected_status = 'cancelled' if cancelled else 'failed'
    await stream_environment.assert_settled(expected_status)
    async with stream_environment.sessions() as session:
        ledger = (await session.execute(select(AiApiUsageLedger))).scalar_one()
        assert ledger.error_code == ('cancelled' if cancelled else 'INTERNAL')
    await response.body_iterator.aclose()
    await stream.aclose()
    assert source.close_count == 1
    await stream_environment.assert_settled(expected_status)


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_stream_read_error_survives_close_cancellation(
    stream_environment: StreamEnvironment, cancelled: bool
) -> None:
    failure = asyncio.CancelledError('read cancelled') if cancelled else ModelServiceError.runtime_error()
    source = CheckpointStream(failure=failure, close_failure=asyncio.CancelledError('close cancelled'))
    stream = await stream_environment.start(source)
    with pytest.raises(type(failure)) as raised:
        await anext(stream)
    assert raised.value is failure
    await stream_environment.assert_settled('cancelled' if cancelled else 'failed')


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_stream_construction_failure_settles_reserved_invocation(
    stream_environment: StreamEnvironment, cancelled: bool
) -> None:
    failure = asyncio.CancelledError('setup cancelled') if cancelled else ModelServiceError.runtime_error()

    async def operation(snapshot: InvocationSnapshotRecord) -> ModelServiceStreamResult:
        del snapshot
        if cancelled:
            request_scope.cancel()
            await anyio.lowlevel.checkpoint()
        raise failure

    with anyio.CancelScope() as request_scope, pytest.raises(type(failure)) as raised:
        await stream_environment.coordinator.run_stream(
            _identity(), MODEL_CODE, 'chat', operation_type='chat', input_bytes=10, operation=operation
        )
    if not cancelled:
        assert raised.value is failure
    await stream_environment.assert_settled('cancelled' if cancelled else 'failed')
    await stream_environment.assert_same_key_can_call_again()


@pytest.mark.asyncio
@pytest.mark.parametrize('send_failure', ['headers', 'content'])
async def test_asgi_24_send_failure_closes_reserved_stream(
    stream_environment: StreamEnvironment, send_failure: str
) -> None:
    source = CheckpointStream()
    stream = await stream_environment.start(source)
    response = streaming_response(openai_stream(stream))

    async def receive() -> Message:
        await anyio.sleep_forever()

    async def send(message: Message) -> None:
        if send_failure == 'headers' or b'partial answer' in message.get('body', b''):
            raise OSError('connection closed')

    scope: Scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}}
    with pytest.raises(ClientDisconnect):
        await response(scope, receive, send)
    await stream_environment.assert_settled()
    assert source.close_count == 1
