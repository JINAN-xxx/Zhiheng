"""Disconnects must finish persistence and release the session for the next send."""

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import NoReturn

import anyio
import httpx
import pytest
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response, StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from plugins.ai.config import LiteLLMSettings
from plugins.ai.controller import ai_chat_controller as chat_controller
from plugins.ai.controller.ai_chat_controller import _serialize_stream
from plugins.ai.domain.model_runtime import ModelStreamEvent
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.do.ai_chat_platform_do import AiChatMessage
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient
from plugins.ai.service.chat_runtime_coordinator import ChatRuntimeCoordinator
from plugins.ai.service.platform_chat_service import PlatformChatService
from tests.plugins.ai.test_chat_runtime_coordinator import FakeRedis
from tests.plugins.ai.test_platform_chat_service import (
    MIN_COMMIT_COUNT,
    SESSION_ID,
    USER_ID,
    FakeCoordinator,
    FakeDao,
    FakeLock,
    FakeModelService,
    FakeRuntimeStream,
    FakeSession,
    _request,
    _service,
)


class CheckpointSession(FakeSession):
    async def commit(self) -> None:
        await anyio.lowlevel.checkpoint()
        await super().commit()


class CheckpointDao(FakeDao):
    async def create_message(self, db: object, message: AiChatMessage) -> AiChatMessage:
        await anyio.lowlevel.checkpoint()
        return await super().create_message(db, message)


class CheckpointLock(FakeLock):
    async def __aexit__(self, exception_type: object, exception: object, traceback: object) -> bool:
        await anyio.lowlevel.checkpoint()
        return await super().__aexit__(exception_type, exception, traceback)


class CheckpointStream(FakeRuntimeStream):
    async def aclose(self) -> None:
        await anyio.lowlevel.checkpoint()
        await super().aclose()


def cancellation_service(
    stream: FakeRuntimeStream | None = None, db: FakeSession | None = None
) -> tuple[PlatformChatService, FakeSession, FakeDao, FakeModelService, FakeCoordinator]:
    coordinator = FakeCoordinator()
    coordinator.lock = CheckpointLock()
    return _service(
        db=db or CheckpointSession(),
        dao=CheckpointDao(),
        coordinator=coordinator,
        stream=stream or CheckpointStream((ModelStreamEvent(event_type='content', delta='partial answer'),)),
    )


@pytest.mark.asyncio
async def test_close_before_first_event_finalizes_pending_run_once() -> None:
    service, _, dao, runtime, coordinator = cancellation_service()
    result = await service.send(USER_ID, _request())

    with anyio.CancelScope() as request_scope:
        request_scope.cancel()
        await result.aclose()
    await result.aclose()

    assert dao.runs[0].status == 'interrupted'
    assert dao.runs[0].finished_at is not None
    assert runtime.stream.close_count == 1
    assert coordinator.lock.exited == 1
    assert len(dao.messages) == 1


@pytest.mark.asyncio
async def test_unstarted_close_failure_still_unlocks_and_does_not_repeat_cleanup() -> None:
    class FailingTerminalSession(CheckpointSession):
        async def commit(self) -> None:
            if self.commit_count == 1:
                raise RuntimeError('terminal write failed')
            await super().commit()

    service, _, _, runtime, coordinator = cancellation_service(db=FailingTerminalSession())
    result = await service.send(USER_ID, _request())
    with pytest.raises(RuntimeError, match='terminal write failed'):
        await result.aclose()
    await result.aclose()

    assert coordinator.lock.exited == 1
    assert runtime.stream.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_mode', ['disconnect', 'send_error', 'discard'])
async def test_controller_owns_unstarted_response_and_same_session_can_send_again(
    monkeypatch: pytest.MonkeyPatch, failure_mode: str
) -> None:
    redis = FakeRedis()
    service, db, dao, runtime, _ = cancellation_service()
    service._coordinator = ChatRuntimeCoordinator(redis)
    monkeypatch.setattr(chat_controller, '_platform_chat_service', lambda request, query_db: service)
    scope: Scope = {
        'type': 'http',
        'asgi': {'version': '3.0', 'spec_version': '2.4' if failure_mode == 'send_error' else '2.3'},
        'method': 'POST',
        'path': '/ai/chat/send',
        'headers': [],
    }
    response = await chat_controller.send_chat_message(
        Request(scope), _request(), db, SimpleNamespace(user=SimpleNamespace(user_id=USER_ID))
    )
    starting = anyio.Event()

    async def receive() -> Message:
        await starting.wait()
        return {'type': 'http.disconnect'}

    async def send(message: Message) -> None:
        if message['type'] == 'http.response.start':
            starting.set()
            if failure_mode == 'send_error':
                raise OSError('client left before response body')
            await anyio.sleep_forever()

    with anyio.fail_after(5):
        if failure_mode == 'discard':
            await response.body_iterator.aclose()
        elif failure_mode == 'send_error':
            with pytest.raises(ClientDisconnect):
                await response(scope, receive, send)
        else:
            await response(scope, receive, send)

    assert dao.runs[0].status == 'interrupted'
    assert await redis.get(f'ai:chat:lock:{SESSION_ID}') is None
    assert runtime.stream.close_count == 1
    service._id_factory = new_resource_id
    runtime.stream = CheckpointStream((ModelStreamEvent(event_type='content', delta='continued'),))
    request = _request(session_id=SESSION_ID).model_copy(update={'client_message_id': 'retry-after-disconnect'})
    continued = await service.send(USER_ID, request)
    events = [event async for event in continued]
    assert events[-1].data['status'] == 'completed'
    assert await redis.get(f'ai:chat:lock:{SESSION_ID}') is None


@pytest.mark.asyncio
async def test_close_inside_cancelled_request_scope_saves_partial_answer_and_releases_lock() -> None:
    service, _, dao, runtime, coordinator = cancellation_service()
    result = await service.send(USER_ID, _request())
    assert (await anext(result)).event_type == 'meta'
    assert (await anext(result)).event_type == 'content'

    with anyio.CancelScope() as request_scope:
        request_scope.cancel()
        await result.aclose()

    assert dao.runs[0].status == 'interrupted'
    assert dao.messages[-1].content == 'partial answer'
    assert dao.messages[-1].status == 'interrupted'
    assert runtime.stream.close_count == 1
    assert coordinator.lock.exited == 1


@pytest.mark.asyncio
async def test_disconnect_while_waiting_for_next_token_finalizes_cancelled_run() -> None:
    waiting = anyio.Event()

    class WaitingStream(CheckpointStream):
        async def __anext__(self) -> ModelStreamEvent:
            waiting.set()
            await anyio.sleep_forever()

    service, _, dao, runtime, coordinator = cancellation_service(WaitingStream(()))
    result = await service.send(USER_ID, _request())
    assert (await anext(result)).event_type == 'meta'

    async def consume() -> None:
        await anext(result)

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(consume)
        await waiting.wait()
        tasks.cancel_scope.cancel()

    assert dao.runs[0].status == 'cancelled'
    assert runtime.stream.close_count == 1
    assert coordinator.lock.exited == 1


@pytest.mark.asyncio
async def test_done_is_sent_only_after_terminal_commit_and_lock_release() -> None:
    service, db, dao, _, coordinator = cancellation_service()
    result = await service.send(USER_ID, _request())
    try:
        while (event := await anext(result)).event_type != 'done':
            pass
        assert event.data['status'] == 'completed'
        assert dao.runs[0].status == 'completed'
        assert db.commit_count == MIN_COMMIT_COUNT
        assert coordinator.lock.exited == 1
    finally:
        await result.aclose()


@pytest.mark.asyncio
async def test_terminal_write_failure_still_releases_lock() -> None:
    class FailingCommitSession(CheckpointSession):
        async def commit(self) -> None:
            if self.commit_count == MIN_COMMIT_COUNT - 1:
                raise RuntimeError('simulated database failure')
            await super().commit()

    service, _, _, _, coordinator = cancellation_service(db=FailingCommitSession())
    result = await service.send(USER_ID, _request())
    with pytest.raises(RuntimeError, match='simulated database failure'):
        _ = [event async for event in result]
    assert coordinator.lock.exited == 1


@pytest.mark.asyncio
async def test_setup_cancellation_marks_pending_run_and_releases_acquired_lock() -> None:
    service, _, dao, runtime, coordinator = cancellation_service()

    async def cancelled_setup(*args, **kwargs) -> NoReturn:
        del args, kwargs
        await anyio.lowlevel.checkpoint()
        raise asyncio.CancelledError

    runtime.stream_chat = cancelled_setup
    with anyio.CancelScope() as request_scope:
        # Cancel when setup reaches the acquired lock, after the run is committed.
        original_enter = coordinator.lock.__class__.__aenter__

        class CancellingLock(CheckpointLock):
            async def __aenter__(self) -> None:
                await original_enter(self)
                request_scope.cancel()

        coordinator.lock = CancellingLock()
        await service.send(USER_ID, _request())

    assert dao.runs[0].status == 'cancelled'
    assert coordinator.lock.exited == 1


@pytest.mark.asyncio
async def test_starlette_disconnect_saves_partial_answer_and_next_send_can_acquire_session() -> None:
    class RedisWithCheckpoint(FakeRedis):
        async def eval(self, script: str, key_count: int, key: str, token: str) -> int:
            await anyio.lowlevel.checkpoint()
            return await super().eval(script, key_count, key, token)

    class PausingStream(CheckpointStream):
        async def __anext__(self) -> ModelStreamEvent:
            try:
                return next(self.events)
            except StopIteration:
                await anyio.sleep_forever()

    redis = RedisWithCheckpoint()
    service, _, dao, runtime, _ = cancellation_service(
        PausingStream((ModelStreamEvent(event_type='content', delta='partial answer'),))
    )
    service._coordinator = ChatRuntimeCoordinator(redis)
    delivered = anyio.Event()
    received_request = False

    async def receive() -> Message:
        nonlocal received_request
        if not received_request:
            received_request = True
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        await delivered.wait()
        return {'type': 'http.disconnect'}

    async def send(message: Message) -> None:
        if message['type'] == 'http.response.body' and b'partial answer' in message.get('body', b''):
            delivered.set()

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        result = await service.send(USER_ID, _request())
        await StreamingResponse(_serialize_stream(result), media_type='text/event-stream')(scope, receive, send)

    async def dispatch(request: Request, call_next: RequestResponseEndpoint) -> Response:
        return await call_next(request)

    app = BaseHTTPMiddleware(endpoint, dispatch=dispatch)
    scope: Scope = {
        'type': 'http',
        'asgi': {'version': '3.0', 'spec_version': '2.3'},
        'http_version': '1.1',
        'method': 'POST',
        'scheme': 'http',
        'path': '/ai/chat/send',
        'root_path': '',
        'query_string': b'',
        'headers': [],
    }
    with anyio.fail_after(5):
        await app(scope, receive, send)

    assert dao.runs[0].status in {'cancelled', 'interrupted'}
    assert dao.messages[-1].content == 'partial answer'
    assert await redis.get(f'ai:chat:lock:{SESSION_ID}') is None

    service._id_factory = new_resource_id
    runtime.stream = CheckpointStream((ModelStreamEvent(event_type='content', delta='continued answer'),))
    request = _request(session_id=SESSION_ID, message='continue').model_copy(update={'client_message_id': 'next-turn'})
    result = await service.send(USER_ID, request)
    events = [event async for event in result]
    assert events[-1].data['status'] == 'completed'
    assert dao.messages[-1].content == 'continued answer'
    assert await redis.get(f'ai:chat:lock:{SESSION_ID}') is None


@pytest.mark.asyncio
async def test_cancelled_upstream_reader_closes_httpx_response_body() -> None:
    waiting = anyio.Event()

    class UpstreamBody(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'data: {"choices":[]}\n\n'
            waiting.set()
            await anyio.sleep_forever()

        async def aclose(self) -> None:
            await anyio.lowlevel.checkpoint()
            self.closed = True

    body = UpstreamBody()

    async def response(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, stream=body)

    client = LiteLLMRuntimeClient(
        LiteLLMSettings(LITELLM_RUNTIME_KEY='test-only'), transport=httpx.MockTransport(response)
    )
    stream = client.stream_chat_completion({'model': 'test-model', 'messages': []})

    async def consume() -> None:
        async for _ in stream:
            pass

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(consume)
            await waiting.wait()
            tasks.cancel_scope.cancel()
        assert body.closed, 'Cancelling the reader must still close the upstream HTTP response'
    finally:
        await client.aclose()
