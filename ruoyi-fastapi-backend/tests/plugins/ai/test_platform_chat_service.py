import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from plugins.ai.domain.model_runtime import ModelStreamEvent, ModelUsage
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.do.ai_chat_platform_do import AiChatMessage, AiChatRun, AiChatSession
from plugins.ai.entity.vo.ai_platform_chat_vo import PlatformChatRequestModel
from plugins.ai.service.chat_runtime_coordinator import ChatCoordinationError
from plugins.ai.service.model_service import ModelServiceStreamResult, ResolvedModel
from plugins.ai.service.platform_chat_service import (
    PlatformChatError,
    PlatformChatEvent,
    PlatformChatService,
    ResolvedPlatformImage,
)

USER_ID = 1001
MODEL_ID = 'mdl_123e4567e89b42d3a456426614174000'
BINDING_ID = 'bnd_987e4567e89b42d3a456426614174111'
SESSION_ID = 'ses_223e4567e89b42d3a456426614174222'
RUN_ID = 'run_323e4567e89b42d3a456426614174333'
CLIENT_MESSAGE_ID = 'client-1'
FILE_ID = UUID('123e4567-e89b-42d3-a456-426614174000')
NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
MESSAGE_COUNT = 2
MIN_COMMIT_COUNT = 3


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self._active = False

    def add(self, item: object) -> None:
        del item
        self._active = True

    async def flush(self) -> None:
        self._active = True

    async def commit(self) -> None:
        self.commit_count += 1
        self._active = False

    async def rollback(self) -> None:
        self.rollback_count += 1
        self._active = False

    def in_transaction(self) -> bool:
        return self._active


class FakeLock:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> None:
        if self.error is not None:
            raise self.error
        self.entered += 1

    async def __aexit__(self, exception_type: object, exception: object, traceback: object) -> bool:
        del exception_type, exception, traceback
        self.exited += 1
        return False


class FakeCoordinator:
    def __init__(self, *, lock_error: BaseException | None = None) -> None:
        self.lock = FakeLock(error=lock_error)
        self.cancelled: set[str] = set()

    def acquire_session(self, session_id: str) -> FakeLock:
        assert session_id
        return self.lock

    async def request_cancel(self, run_id: str) -> None:
        self.cancelled.add(run_id)

    async def is_cancelled(self, run_id: str) -> bool:
        return run_id in self.cancelled


class FakeRuntimeStream:
    def __init__(self, events: tuple[ModelStreamEvent, ...], error: BaseException | None = None) -> None:
        self.events = iter(events)
        self.error = error
        self.close_count = 0

    def __aiter__(self) -> 'FakeRuntimeStream':
        return self

    async def __anext__(self) -> ModelStreamEvent:
        if self.error is not None:
            error = self.error
            self.error = None
            raise error
        try:
            return next(self.events)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.close_count += 1


class FakeModelService:
    def __init__(self, stream: FakeRuntimeStream, *, stream_error: BaseException | None = None) -> None:
        self.stream = stream
        self.stream_error = stream_error
        self.resolve_calls: list[str] = []
        self.stream_calls: list[tuple[str, tuple[object, ...], Mapping[str, object]]] = []

    async def resolve_model(self, model_id: str) -> ResolvedModel:
        self.resolve_calls.append(model_id)
        return ResolvedModel(
            platform_model_id=MODEL_ID,
            binding_id=BINDING_ID,
            engine_type='litellm',
            engine_model_snapshot='gpt-4o-mini',
            default_parameters={'temperature': 0.2},
        )

    async def stream_chat(
        self,
        model_id: str,
        messages: tuple[object, ...],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceStreamResult:
        self.stream_calls.append((model_id, messages, parameters or {}))
        if self.stream_error is not None:
            raise self.stream_error
        return ModelServiceStreamResult(
            platform_model_id=MODEL_ID,
            binding_id=BINDING_ID,
            engine_type='litellm',
            engine_model_snapshot='gpt-4o-mini',
            _stream=self.stream,
        )


class FakeDao:
    def __init__(self, session: AiChatSession | None = None) -> None:
        self.session = session
        self.messages: list[AiChatMessage] = []
        self.runs: list[AiChatRun] = []
        self.transitions: list[tuple[str, set[str], dict[str, object]]] = []

    async def get_owned_session_for_update(self, db: object, session_id: str, *, user_id: int) -> AiChatSession | None:
        del db
        if self.session is not None and self.session.session_id == session_id and self.session.user_id == user_id:
            return self.session
        return None

    async def create_session(self, db: object, session: AiChatSession) -> AiChatSession:
        del db
        self.session = session
        return session

    async def get_message_by_client_id(
        self,
        db: object,
        session_id: str,
        client_message_id: str,
    ) -> AiChatMessage | None:
        del db
        return next(
            (
                message
                for message in self.messages
                if message.session_id == session_id and message.client_message_id == client_message_id
            ),
            None,
        )

    async def create_message(self, db: object, message: AiChatMessage) -> AiChatMessage:
        del db
        self.messages.append(message)
        return message

    async def list_history_messages(self, db: object, session_id: str, *, limit: int) -> list[AiChatMessage]:
        del db, limit
        return [
            message for message in self.messages if message.session_id == session_id and message.status == 'complete'
        ]

    async def create_run(self, db: object, run: AiChatRun) -> AiChatRun:
        del db
        self.runs.append(run)
        return run

    async def get_latest_run(self, db: object, session_id: str) -> AiChatRun | None:
        del db
        for run in reversed(self.runs):
            if run.session_id == session_id:
                return run
        return None

    async def transition_run_status(
        self,
        db: object,
        run_id: str,
        *,
        expected_statuses: set[str],
        values: dict[str, object],
    ) -> int:
        del db
        self.transitions.append((run_id, expected_statuses, values))
        for run in self.runs:
            if run.run_id == run_id and run.status in expected_statuses:
                for key, value in values.items():
                    setattr(run, key, value)
                return 1
        return 0


class FakePreferences:
    async def get_preferences(self, *, user_id: int) -> SimpleNamespace:
        assert user_id == USER_ID
        return SimpleNamespace(
            temperature=0.2,
            add_history=True,
            history_message_count=20,
            system_prompt='You are a platform assistant.',
            vision_enabled=True,
            image_max_size_mb=10,
        )


class FakeImages:
    def __init__(self, image: ResolvedPlatformImage | None = None) -> None:
        self.image = image
        self.calls: list[tuple[int, UUID, int]] = []

    async def resolve(self, user_id: int, file_id: UUID, *, max_size_bytes: int) -> ResolvedPlatformImage:
        self.calls.append((user_id, file_id, max_size_bytes))
        if self.image is None:
            raise PlatformChatError.image_invalid()
        return self.image


def _request(
    *, session_id: str | None = None, message: str = 'hello', images: list[dict[str, str]] | None = None
) -> PlatformChatRequestModel:
    return PlatformChatRequestModel.model_validate(
        {
            'sessionId': session_id,
            'modelId': MODEL_ID,
            'clientMessageId': CLIENT_MESSAGE_ID,
            'message': message,
            'images': images or [],
        }
    )


def _service(
    *,
    stream: FakeRuntimeStream | None = None,
    dao: FakeDao | None = None,
    coordinator: FakeCoordinator | None = None,
    images: FakeImages | None = None,
    stream_error: BaseException | None = None,
) -> tuple[PlatformChatService, FakeSession, FakeDao, FakeModelService, FakeCoordinator]:
    db = FakeSession()
    fake_dao = dao or FakeDao()
    fake_runtime = FakeModelService(
        stream,
        stream_error=stream_error,
    )
    if stream is None:
        fake_runtime.stream = FakeRuntimeStream(
            (
                ModelStreamEvent(event_type='content', delta='answer'),
                ModelStreamEvent(event_type='reasoning', delta='because'),
                ModelStreamEvent(
                    event_type='usage',
                    usage=ModelUsage(input_tokens=2, output_tokens=3, reasoning_tokens=1),
                ),
            )
        )
    fake_coordinator = coordinator or FakeCoordinator()
    service = PlatformChatService(
        db,
        model_service=fake_runtime,
        coordinator=fake_coordinator,
        preferences=FakePreferences(),
        chat_dao=fake_dao,
        image_resolver=images or FakeImages(),
        id_factory=lambda prefix: {'ses': SESSION_ID, 'msg': 'msg_423e4567e89b42d3a456426614174444', 'run': RUN_ID}[
            prefix
        ],
        clock=lambda: NOW,
    )
    return service, db, fake_dao, fake_runtime, fake_coordinator


@pytest.mark.asyncio
async def test_send_persists_user_run_and_terminal_assistant_with_platform_events() -> None:
    service, db, dao, runtime, coordinator = _service()

    result = await service.send(USER_ID, _request())
    events = [event async for event in result]

    assert events == [
        PlatformChatEvent('meta', {'sessionId': SESSION_ID, 'runId': RUN_ID, 'bindingId': BINDING_ID}),
        PlatformChatEvent('content', {'sessionId': SESSION_ID, 'runId': RUN_ID, 'delta': 'answer'}),
        PlatformChatEvent('reasoning', {'sessionId': SESSION_ID, 'runId': RUN_ID, 'delta': 'because'}),
        PlatformChatEvent(
            'usage',
            {'sessionId': SESSION_ID, 'runId': RUN_ID, 'inputTokens': 2, 'outputTokens': 3},
        ),
        PlatformChatEvent('done', {'sessionId': SESSION_ID, 'runId': RUN_ID, 'status': 'completed'}),
    ]
    assert dao.session is not None
    assert dao.session.model_id == MODEL_ID
    assert len(dao.messages) == MESSAGE_COUNT
    assert dao.messages[0].role == 'user'
    assert dao.messages[1].role == 'assistant'
    assert dao.messages[1].status == 'complete'
    assert dao.messages[1].content == 'answer'
    assert dao.messages[1].reasoning_content == 'because'
    assert dao.runs[0].status == 'completed'
    assert db.commit_count >= MIN_COMMIT_COUNT
    assert coordinator.lock.entered == 1
    assert coordinator.lock.exited == 1
    assert runtime.stream_calls


@pytest.mark.asyncio
async def test_existing_session_is_owned_and_cannot_change_model() -> None:
    session = AiChatSession(
        session_id=SESSION_ID,
        user_id=USER_ID,
        model_id='mdl_423e4567e89b42d3a456426614174444',
        status='active',
    )
    service, _, _, runtime, _ = _service(dao=FakeDao(session=session))

    with pytest.raises(PlatformChatError) as raised:
        await service.send(USER_ID, _request(session_id=SESSION_ID))

    assert raised.value.code == 'CHAT_SESSION_MODEL_MISMATCH'
    assert runtime.stream_calls == []


@pytest.mark.asyncio
async def test_duplicate_client_message_replays_without_upstream_or_new_run() -> None:
    dao = FakeDao(session=AiChatSession(session_id=SESSION_ID, user_id=USER_ID, model_id=MODEL_ID, status='active'))
    dao.messages.append(
        AiChatMessage(
            message_id='msg_423e4567e89b42d3a456426614174444',
            session_id=SESSION_ID,
            client_message_id=CLIENT_MESSAGE_ID,
            role='user',
            content='hello',
            images=[],
            status='complete',
        )
    )
    service, _, _, runtime, coordinator = _service(dao=dao)

    result = await service.send(USER_ID, _request(session_id=SESSION_ID))
    events = [event async for event in result]

    assert events[-1].event_type == 'done'
    assert runtime.stream_calls == []
    assert dao.runs == []
    assert coordinator.lock.entered == 0


@pytest.mark.asyncio
async def test_busy_or_redis_failure_is_http_error_before_runtime_stream() -> None:
    coordinator = FakeCoordinator(lock_error=ChatCoordinationError.session_busy())
    service, _, dao, runtime, _ = _service(coordinator=coordinator)

    with pytest.raises(ChatCoordinationError) as raised:
        await service.send(USER_ID, _request())

    assert raised.value.code == 'CHAT_SESSION_BUSY'
    assert runtime.stream_calls == []
    assert dao.runs[0].status == 'failed'


@pytest.mark.asyncio
async def test_runtime_stream_construction_failure_is_before_sse_and_marks_run_failed() -> None:
    runtime_error = ModelServiceError.runtime_unavailable()
    service, _, dao, runtime, coordinator = _service(stream_error=runtime_error)

    with pytest.raises(ModelServiceError) as raised:
        await service.send(USER_ID, _request())

    assert raised.value is runtime_error
    assert runtime.stream_calls
    assert dao.runs[0].status == 'failed'
    assert coordinator.lock.entered == 1
    assert coordinator.lock.exited == 1


@pytest.mark.asyncio
async def test_client_close_after_partial_response_persists_interrupted_assistant() -> None:
    service, _, dao, _, coordinator = _service(
        stream=FakeRuntimeStream((ModelStreamEvent(event_type='content', delta='partial'),))
    )

    result = await service.send(USER_ID, _request())
    assert (await anext(result)).event_type == 'meta'
    assert (await anext(result)).event_type == 'content'
    await result.aclose()

    assert dao.runs[0].status == 'interrupted'
    assert dao.messages[-1].status == 'interrupted'
    assert dao.messages[-1].content == 'partial'
    assert coordinator.lock.exited == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('error', (ModelServiceError.runtime_timeout(), asyncio.CancelledError()))
async def test_runtime_error_or_cancellation_finalizes_run_without_assistant_duplication(
    error: BaseException,
) -> None:
    service, _, dao, _runtime, _ = _service(stream=FakeRuntimeStream((), error=error))

    result = await service.send(USER_ID, _request())
    if isinstance(error, asyncio.CancelledError):
        assert (await anext(result)).event_type == 'meta'
        with pytest.raises(asyncio.CancelledError):
            await anext(result)
        assert dao.runs[0].status == 'cancelled'
    else:
        events = [event async for event in result]
        assert events[-2].event_type == 'error'
        assert events[-1].event_type == 'done'
        assert dao.runs[0].status == 'failed'
        assert len(dao.messages) == 1


@pytest.mark.asyncio
async def test_image_reference_becomes_controlled_data_url_content_block() -> None:
    image = ResolvedPlatformImage(
        file_id=FILE_ID,
        mime_type='image/png',
        size_bytes=3,
        data_url='data:image/png;base64,AAAA',
    )
    images = FakeImages(image)
    service, _, _, runtime, _ = _service(images=images)

    await service.send(USER_ID, _request(images=[{'fileId': str(FILE_ID)}]))

    message = runtime.stream_calls[0][1][-1]
    assert list(message.content) == [
        {'type': 'text', 'text': 'hello'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}},
    ]
    assert images.calls == [(USER_ID, FILE_ID, 10 * 1024 * 1024)]
