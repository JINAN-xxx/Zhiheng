import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta, timezone
from itertools import count
from types import SimpleNamespace
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.status import HTTP_404_NOT_FOUND, HTTP_409_CONFLICT

from plugins.ai.dao.platform_chat_dao import PlatformChatDao
from plugins.ai.domain.model_runtime import ModelStreamEvent, ModelUsage
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.do.ai_chat_platform_do import AiChatMessage, AiChatPreferences, AiChatRun, AiChatSession
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.entity.vo.ai_platform_chat_vo import PlatformChatRequestModel
from plugins.ai.infrastructure.chat_knowledge_adapter import ChatKnowledgeAdapter
from plugins.ai.service.chat_preferences_service import ChatPreferencesService
from plugins.ai.service.chat_runtime_coordinator import ChatCoordinationError
from plugins.ai.service.model_service import ModelServiceStreamResult, ResolvedModel
from plugins.ai.service.platform_chat_service import (
    PlatformChatError,
    PlatformChatEvent,
    PlatformChatService,
    ResolvedChatKnowledgePassage,
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
        self.pending_session: AiChatSession | None = None

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
        self.pending_session = None

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

    async def resolve_model(self, model_id: str, expected_type: str | None = None) -> ResolvedModel:
        del expected_type
        self.resolve_calls.append(model_id)
        return ResolvedModel(
            platform_model_id=MODEL_ID,
            binding_id=BINDING_ID,
            engine_type='litellm',
            engine_model_snapshot='gpt-4o-mini',
            model_type='chat',
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
        self.session_mutations: list[tuple[str, object]] = []

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

    async def update_session(
        self,
        db: object,
        session_id: str,
        *,
        user_id: int,
        values: dict[str, object],
    ) -> int:
        del db
        if self.session is None or self.session.session_id != session_id or self.session.user_id != user_id:
            return 0
        for key, value in values.items():
            setattr(self.session, key, value)
        self.session_mutations.append(('update', dict(values)))
        return 1

    async def delete_runs(self, db: object, session_id: str) -> int:
        del db
        self.session_mutations.append(('delete_runs', session_id))
        count = len([run for run in self.runs if run.session_id == session_id])
        self.runs = [run for run in self.runs if run.session_id != session_id]
        return count

    async def delete_messages(self, db: object, session_id: str) -> int:
        del db
        self.session_mutations.append(('delete_messages', session_id))
        count = len([message for message in self.messages if message.session_id == session_id])
        self.messages = [message for message in self.messages if message.session_id != session_id]
        return count

    async def delete_session(self, db: object, session_id: str, *, user_id: int) -> int:
        del db
        self.session_mutations.append(('delete_session', session_id))
        if self.session is None or self.session.session_id != session_id or self.session.user_id != user_id:
            return 0
        self.session = None
        return 1

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


class RollbackPreferences(FakePreferences):
    def __init__(self, db: FakeSession) -> None:
        self._db = db

    async def get_preferences(self, *, user_id: int) -> SimpleNamespace:
        result = await super().get_preferences(user_id=user_id)
        await self._db.rollback()
        return result


class TransactionalFakeDao(FakeDao):
    async def create_session(self, db: FakeSession, session: AiChatSession) -> AiChatSession:
        result = await super().create_session(db, session)
        db.pending_session = session
        return result

    async def create_message(self, db: FakeSession, message: AiChatMessage) -> AiChatMessage:
        if db.pending_session is None or db.pending_session.session_id != message.session_id:
            raise AssertionError('message foreign key session is missing')
        return await super().create_message(db, message)


@pytest.mark.asyncio
async def test_list_sessions_builds_page_model_with_public_aliases() -> None:
    class SessionListDao:
        async def list_sessions(
            self, db: object, *, user_id: int, page_num: int, page_size: int, status: str = 'active'
        ) -> SimpleNamespace:
            del db
            assert user_id == USER_ID
            assert status == 'active'
            return SimpleNamespace(
                rows=[
                    AiChatSession(
                        session_id=SESSION_ID,
                        user_id=USER_ID,
                        model_id=MODEL_ID,
                        title='测试会话',
                        status='active',
                        created_at=NOW,
                        updated_at=NOW,
                    )
                ],
                page_num=page_num,
                page_size=page_size,
                total=1,
                has_next=False,
            )

    service, _, _, _, _ = _service()
    service._dao = SessionListDao()  # type: ignore[assignment]

    result = await service.list_sessions(USER_ID, page_num=1, page_size=20)

    assert result.model_dump(by_alias=True) == {
        'rows': [
            {
                'sessionId': SESSION_ID,
                'modelId': MODEL_ID,
                'title': '测试会话',
                'status': 'active',
                'createdAt': NOW,
                'updatedAt': NOW,
            }
        ],
        'pageNum': 1,
        'pageSize': 20,
        'total': 1,
        'hasNext': False,
    }


@pytest_asyncio.fixture
async def archive_db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        async with engine.begin() as connection:
            await connection.execute(text('PRAGMA foreign_keys=ON'))
            for table in (
                AiModelCatalog.__table__,
                AiResourceBinding.__table__,
                AiChatSession.__table__,
                AiChatMessage.__table__,
                AiChatRun.__table__,
                AiChatPreferences.__table__,
            ):
                await connection.run_sync(table.create)
        session_maker = async_sessionmaker(engine, expire_on_commit=True)
        async with session_maker() as db:
            db.add(
                AiModelCatalog(
                    model_id=MODEL_ID,
                    model_code='archive-test-model',
                    display_name='Archive Test Model',
                    model_type='chat',
                    capabilities={},
                    default_parameters={},
                    lifecycle_status='active',
                    published_at=NOW,
                    create_by='tester',
                    create_time=NOW,
                    update_by='tester',
                    update_time=NOW,
                )
            )
            db.add(
                AiResourceBinding(
                    binding_id=BINDING_ID,
                    resource_type='model',
                    resource_id=MODEL_ID,
                    engine_type='litellm',
                    engine_resource_id='gpt-4o-mini',
                    binding_status='active',
                    engine_metadata={},
                    version=1,
                    create_by='tester',
                    create_time=NOW,
                    update_by='tester',
                    update_time=NOW,
                )
            )
            await db.commit()
            yield db
    finally:
        await engine.dispose()


def _archive_service(db: AsyncSession) -> tuple[PlatformChatService, FakeModelService]:
    runtime = FakeModelService(FakeRuntimeStream((ModelStreamEvent(event_type='content', delta='answer'),)))
    clock_ticks = count()
    return (
        PlatformChatService(
            db,
            model_service=runtime,
            coordinator=FakeCoordinator(),
            preferences=ChatPreferencesService(db),
            clock=lambda: NOW.replace(tzinfo=None) + timedelta(microseconds=next(clock_ticks)),
        ),
        runtime,
    )


@pytest.mark.asyncio
async def test_archive_read_restore_preserves_session_messages_runs_and_continuation(archive_db: AsyncSession) -> None:
    service, runtime = _archive_service(archive_db)
    stream = await service.send(USER_ID, _request())
    [event async for event in stream]
    session_id = stream.session_id
    before = await service.get_session_detail(USER_ID, session_id)
    service._clock = lambda: NOW.replace(tzinfo=None) + timedelta(minutes=1)

    archived = await service.set_session_status(USER_ID, session_id, 'archived')

    assert not archive_db.in_transaction()
    assert archived.status == 'archived'
    assert archived.updated_at > before.session.updated_at
    assert archived.model_id == before.session.model_id
    assert archived.title == before.session.title
    assert archived.created_at == before.session.created_at
    detail = await service.get_session_detail(USER_ID, session_id)
    assert detail.session == archived
    assert detail.messages == before.messages
    assert detail.runs == before.runs
    assert (await service.list_sessions(USER_ID, page_num=1, page_size=20)).rows == []
    archived_page = await service.list_sessions(USER_ID, status='archived', page_num=1, page_size=20)
    assert archived_page.total == 1
    assert archived_page.rows == [archived]
    with pytest.raises(PlatformChatError) as raised:
        await service.send(USER_ID, _request(session_id=session_id))
    assert raised.value.code == 'CHAT_SESSION_ARCHIVED'
    assert raised.value.status_code == HTTP_409_CONFLICT
    assert '恢复' in raised.value.public_message
    assert len(runtime.stream_calls) == 1

    service._clock = lambda: NOW.replace(tzinfo=None) + timedelta(minutes=2)
    restored = await service.set_session_status(USER_ID, session_id, 'active')

    assert restored.status == 'active'
    assert restored.updated_at > archived.updated_at
    assert (await service.list_sessions(USER_ID, status='archived', page_num=1, page_size=20)).rows == []
    assert (await service.list_sessions(USER_ID, page_num=1, page_size=20)).rows == [restored]
    runtime.stream = FakeRuntimeStream((ModelStreamEvent(event_type='content', delta='continued'),))
    request = _request(session_id=session_id).model_copy(update={'client_message_id': 'client-2', 'message': 'next'})
    continuation = await service.send(USER_ID, request)
    [event async for event in continuation]
    after = await service.get_session_detail(USER_ID, session_id)
    assert after.session.model_id == before.session.model_id
    assert after.messages[:MESSAGE_COUNT] == before.messages
    assert len(after.messages) == MESSAGE_COUNT * 2
    assert after.runs[0] == before.runs[0]
    assert len(after.runs) == len(before.runs) + 1
    assert [message.content for message in runtime.stream_calls[-1][1][-3:]] == ['hello', 'answer', 'next']


@pytest.mark.asyncio
@pytest.mark.parametrize('initial_status', ['active', 'archived'])
async def test_same_session_status_is_idempotent_and_releases_transaction(
    archive_db: AsyncSession, initial_status: str
) -> None:
    service, _ = _archive_service(archive_db)
    archive_db.add(
        AiChatSession(
            session_id=SESSION_ID,
            user_id=USER_ID,
            model_id=MODEL_ID,
            title='unchanged',
            status=initial_status,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await archive_db.commit()
    before = await service.get_session_detail(USER_ID, SESSION_ID)
    service._clock = lambda: NOW.replace(tzinfo=None) + timedelta(days=1)

    result = await service.set_session_status(USER_ID, SESSION_ID, initial_status)

    assert not archive_db.in_transaction()
    assert result == before.session
    assert (await service.get_session_detail(USER_ID, SESSION_ID)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('target_status', ['active', 'archived'])
async def test_session_status_updates_hide_foreign_and_missing_sessions(
    archive_db: AsyncSession, target_status: str
) -> None:
    service, _ = _archive_service(archive_db)
    archive_db.add(
        AiChatSession(
            session_id=SESSION_ID,
            user_id=USER_ID + 1,
            model_id=MODEL_ID,
            title='private',
            status='archived',
            created_at=NOW,
            updated_at=NOW,
        )
    )
    await archive_db.commit()
    for session_id in (SESSION_ID, 'ses_00000000000040008000000000000001'):
        with pytest.raises(PlatformChatError) as raised:
            await service.set_session_status(USER_ID, session_id, target_status)
        assert raised.value.status_code == HTTP_404_NOT_FOUND
        assert raised.value.code == 'CHAT_SESSION_NOT_FOUND'
        assert not archive_db.in_transaction()
    with pytest.raises(PlatformChatError) as raised:
        await service.send(USER_ID, _request(session_id=SESSION_ID))
    assert raised.value.code == 'CHAT_SESSION_NOT_FOUND'
    assert raised.value.status_code == HTTP_404_NOT_FOUND
    with pytest.raises(PlatformChatError) as raised:
        await service.get_session_detail(USER_ID, SESSION_ID)
    assert raised.value.code == 'CHAT_SESSION_NOT_FOUND'
    owned_detail = await service.get_session_detail(USER_ID + 1, SESSION_ID)
    assert owned_detail.session.status == 'archived'
    assert owned_detail.session.updated_at == NOW.replace(tzinfo=None)


@pytest.mark.asyncio
@pytest.mark.parametrize('run_status', ['pending', 'running'])
async def test_archiving_rejects_any_active_run_without_mutating_history(
    archive_db: AsyncSession, run_status: str
) -> None:
    service, _ = _archive_service(archive_db)
    stream = await service.send(USER_ID, _request())
    [event async for event in stream]
    await PlatformChatDao.transition_run_status(
        archive_db, stream.run_id, expected_statuses={'completed'}, values={'status': run_status}
    )
    archive_db.add(
        AiChatRun(
            run_id=RUN_ID,
            session_id=stream.session_id,
            model_id=MODEL_ID,
            binding_id=BINDING_ID,
            engine_type='litellm',
            engine_model_snapshot='gpt-4o-mini',
            status='failed',
            created_at=NOW + timedelta(minutes=1),
            updated_at=NOW + timedelta(minutes=1),
        )
    )
    await archive_db.commit()
    before = await service.get_session_detail(USER_ID, stream.session_id)

    with pytest.raises(ChatCoordinationError) as raised:
        await service.set_session_status(USER_ID, stream.session_id, 'archived')

    assert raised.value.code == 'CHAT_SESSION_BUSY'
    assert not archive_db.in_transaction()
    assert await service.get_session_detail(USER_ID, stream.session_id) == before


def test_archived_session_error_has_fixed_immutable_public_contract() -> None:
    error = PlatformChatError.session_archived()
    assert error.status_code == HTTP_409_CONFLICT
    assert error.public_dict() == {'code': 'CHAT_SESSION_ARCHIVED', 'message': '对话会话已归档，请先恢复后再发送消息'}
    with pytest.raises(AttributeError):
        error.code = 'CHANGED'
    with pytest.raises(TypeError):
        PlatformChatError._ERRORS['session_archived'] = (200, 'CHANGED', 'unexpected')


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
    *,
    session_id: str | None = None,
    message: str = 'hello',
    images: list[dict[str, str]] | None = None,
    skill_slugs: list[str] | None = None,
    knowledge_base_ids: list[str] | None = None,
) -> PlatformChatRequestModel:
    return PlatformChatRequestModel.model_validate(
        {
            'sessionId': session_id,
            'modelId': MODEL_ID,
            'clientMessageId': CLIENT_MESSAGE_ID,
            'message': message,
            'images': images or [],
            'skillSlugs': skill_slugs or [],
            'knowledgeBaseIds': knowledge_base_ids or [],
        }
    )


class FakeSkills:
    def __init__(self) -> None:
        self.calls: list[tuple[int, list[str]]] = []

    async def resolve(self, user_id: int, slugs: list[str]) -> SimpleNamespace:
        self.calls.append((user_id, slugs))
        return SimpleNamespace(
            system_prompt='<selected_skills>深度研究</selected_skills>',
            snapshots=({'slug': 'deep-research', 'name': '深度研究'},),
        )


def _service(
    *,
    db: FakeSession | None = None,
    stream: FakeRuntimeStream | None = None,
    dao: FakeDao | None = None,
    coordinator: FakeCoordinator | None = None,
    images: FakeImages | None = None,
    stream_error: BaseException | None = None,
    preferences: FakePreferences | None = None,
    skills: FakeSkills | None = None,
) -> tuple[PlatformChatService, FakeSession, FakeDao, FakeModelService, FakeCoordinator]:
    db = db or FakeSession()
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
        preferences=preferences or FakePreferences(),
        chat_dao=fake_dao,
        image_resolver=images or FakeImages(),
        skill_resolver=skills,
        id_factory=lambda prefix: {'ses': SESSION_ID, 'msg': 'msg_423e4567e89b42d3a456426614174444', 'run': RUN_ID}[
            prefix
        ],
        clock=lambda: NOW,
    )
    return service, db, fake_dao, fake_runtime, fake_coordinator


@pytest.mark.asyncio
async def test_send_injects_selected_skills_and_persists_snapshot() -> None:
    skills = FakeSkills()
    service, _, dao, runtime, _ = _service(skills=skills)

    result = await service.send(USER_ID, _request(skill_slugs=['deep-research']))
    [event async for event in result]

    assert skills.calls == [(USER_ID, ['deep-research'])]
    sent_messages = runtime.stream_calls[0][1]
    assert sent_messages[0].role == 'system'
    assert sent_messages[0].content.startswith('<selected_skills>')
    assert 'You are a platform assistant.' in sent_messages[0].content
    assert dao.messages[0].skill_snapshots == [{'slug': 'deep-research', 'name': '深度研究'}]


@pytest.mark.asyncio
async def test_send_without_skills_does_not_require_skill_resolver() -> None:
    service, _, dao, runtime, _ = _service()

    result = await service.send(USER_ID, _request())
    [event async for event in result]

    assert dao.messages[0].skill_snapshots == []
    assert runtime.stream_calls[0][1][0].content == 'You are a platform assistant.'


@pytest.mark.asyncio
async def test_send_with_skills_requires_registered_skill_resolver() -> None:
    service, _, _, _, _ = _service()

    with pytest.raises(PlatformChatError) as raised:
        await service.send(USER_ID, _request(skill_slugs=['deep-research']))

    assert raised.value.code == 'CHAT_SKILL_UNAVAILABLE'


def test_chat_request_rejects_duplicate_or_too_many_skills() -> None:
    with pytest.raises(ValueError, match='重复'):
        _request(skill_slugs=['deep-research', 'deep-research'])
    with pytest.raises(ValueError, match='最多选择 5 个技能'):
        _request(skill_slugs=[f'skill-{index}' for index in range(6)])


@pytest.mark.asyncio
async def test_duplicate_client_message_rejects_changed_skill_selection() -> None:
    dao = FakeDao(session=AiChatSession(session_id=SESSION_ID, user_id=USER_ID, model_id=MODEL_ID, status='active'))
    dao.messages.append(
        AiChatMessage(
            message_id='msg_423e4567e89b42d3a456426614174444',
            session_id=SESSION_ID,
            client_message_id=CLIENT_MESSAGE_ID,
            role='user',
            content='hello',
            images=[],
            skill_snapshots=[{'slug': 'document-summary', 'name': '文档总结'}],
            status='complete',
        )
    )
    skills = FakeSkills()
    service, _, _, _, _ = _service(dao=dao, skills=skills)

    with pytest.raises(PlatformChatError) as raised:
        await service.send(USER_ID, _request(session_id=SESSION_ID, skill_slugs=['deep-research']))

    assert raised.value.code == 'CHAT_IDEMPOTENCY_CONFLICT'


@pytest.mark.asyncio
async def test_rename_session_updates_owned_title_and_commits() -> None:
    dao = FakeDao(
        session=AiChatSession(
            session_id=SESSION_ID,
            user_id=USER_ID,
            model_id=MODEL_ID,
            title='旧标题',
            status='active',
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service, db, _, _, _ = _service(dao=dao)

    result = await service.rename_session(USER_ID, SESSION_ID, '新标题')

    assert result.title == '新标题'
    assert dao.session_mutations[0][0] == 'update'
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_delete_session_removes_runs_messages_then_session_and_commits() -> None:
    dao = FakeDao(session=AiChatSession(session_id=SESSION_ID, user_id=USER_ID, model_id=MODEL_ID, title='待删除'))
    dao.runs.append(
        AiChatRun(
            run_id=RUN_ID,
            session_id=SESSION_ID,
            model_id=MODEL_ID,
            binding_id=BINDING_ID,
            engine_type='litellm',
            engine_model_snapshot='model',
            status='completed',
        )
    )
    dao.messages.append(
        AiChatMessage(
            message_id='msg_423e4567e89b42d3a456426614174444',
            session_id=SESSION_ID,
            client_message_id='delete-1',
            role='user',
            content='hello',
            images=[],
            status='complete',
        )
    )
    service, db, _, _, _ = _service(dao=dao)

    await service.delete_session(USER_ID, SESSION_ID)

    assert [event[0] for event in dao.session_mutations] == ['delete_runs', 'delete_messages', 'delete_session']
    assert dao.session is None
    assert dao.runs == []
    assert dao.messages == []
    assert db.commit_count == 1


@pytest.mark.asyncio
async def test_delete_session_rejects_running_run_without_mutation() -> None:
    dao = FakeDao(session=AiChatSession(session_id=SESSION_ID, user_id=USER_ID, model_id=MODEL_ID, title='运行中'))
    dao.runs.append(
        AiChatRun(
            run_id=RUN_ID,
            session_id=SESSION_ID,
            model_id=MODEL_ID,
            binding_id=BINDING_ID,
            engine_type='litellm',
            engine_model_snapshot='model',
            status='running',
        )
    )
    service, db, _, _, _ = _service(dao=dao)
    db._active = True

    with pytest.raises(ChatCoordinationError) as raised:
        await service.delete_session(USER_ID, SESSION_ID)

    assert raised.value.code == 'CHAT_SESSION_BUSY'
    assert dao.session_mutations == []
    assert db.rollback_count == 1


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
async def test_send_reads_preferences_before_creating_new_session() -> None:
    db = FakeSession()
    dao = TransactionalFakeDao()
    service, _, _, _, _ = _service(db=db, dao=dao, preferences=RollbackPreferences(db))

    result = await service.send(USER_ID, _request())
    [event async for event in result]

    assert dao.session is not None
    assert len(dao.messages) == MESSAGE_COUNT


@pytest.mark.asyncio
@pytest.mark.parametrize('knowledge_base_ids', [[], ['kb-1']])
async def test_send_does_not_access_expired_entities_after_initial_commit(knowledge_base_ids: list[str]) -> None:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    try:
        async with engine.begin() as connection:
            await connection.execute(text('PRAGMA foreign_keys=ON'))
            for table in (
                AiModelCatalog.__table__,
                AiResourceBinding.__table__,
                AiChatSession.__table__,
                AiChatMessage.__table__,
                AiChatRun.__table__,
                AiChatPreferences.__table__,
            ):
                await connection.run_sync(table.create)
        session_maker = async_sessionmaker(engine, expire_on_commit=True)
        async with session_maker() as db:
            db.add(
                AiModelCatalog(
                    model_id=MODEL_ID,
                    model_code='platform-chat-model',
                    display_name='Platform Chat Model',
                    model_type='chat',
                    capabilities={},
                    default_parameters={},
                    lifecycle_status='active',
                    published_at=NOW,
                    create_by='tester',
                    create_time=NOW,
                    update_by='tester',
                    update_time=NOW,
                )
            )
            db.add(
                AiResourceBinding(
                    binding_id=BINDING_ID,
                    resource_type='model',
                    resource_id=MODEL_ID,
                    engine_type='litellm',
                    engine_resource_id='gpt-4o-mini',
                    binding_status='active',
                    engine_metadata={},
                    version=1,
                    create_by='tester',
                    create_time=NOW,
                    update_by='tester',
                    update_time=NOW,
                )
            )
            await db.commit()

            class KnowledgeRuntime:
                async def retrieve_context(self, **kwargs: object) -> list[object]:
                    assert not db.in_transaction()
                    assert kwargs['knowledge_base_ids'] == knowledge_base_ids
                    return []

            service = PlatformChatService(
                db,
                model_service=FakeModelService(FakeRuntimeStream(())),
                knowledge_resolver=ChatKnowledgeAdapter(KnowledgeRuntime()),
                coordinator=FakeCoordinator(),
                preferences=ChatPreferencesService(db),
                id_factory=lambda prefix: {
                    'ses': SESSION_ID,
                    'msg': 'msg_423e4567e89b42d3a456426614174444',
                    'run': RUN_ID,
                }[prefix],
                clock=lambda: NOW,
            )

            result = await service.send(USER_ID, _request(knowledge_base_ids=knowledge_base_ids))
            [event async for event in result]
            persisted_messages = await PlatformChatDao.list_messages(db, SESSION_ID)
            assert persisted_messages[0].knowledge_base_ids == knowledge_base_ids

            persisted_session = await PlatformChatDao.get_owned_session(db, SESSION_ID, user_id=USER_ID)
            persisted_runs = await PlatformChatDao.list_runs(db, SESSION_ID)

        assert persisted_session is not None
        assert len(persisted_runs) == 1
        assert persisted_runs[0].status == 'completed'
    finally:
        await engine.dispose()


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


@pytest.mark.asyncio
async def test_send_retrieves_before_writes_and_preserves_skills_and_history() -> None:
    skills = FakeSkills()
    service, _, dao, runtime, _ = _service(skills=skills)

    class KnowledgeRuntime:
        async def retrieve_context(self, **kwargs: object) -> list[SimpleNamespace]:
            assert dao.session is None
            assert dao.messages == []
            assert dao.runs == []
            assert kwargs['owner_user_id'] == USER_ID
            assert kwargs['knowledge_base_ids'] == ['kb-1']
            return [
                SimpleNamespace(
                    content='Private reference',
                    source_id='doc-1',
                    title='Guide',
                    knowledge_base_id='kb-1',
                )
            ]

    service._knowledge_resolver = ChatKnowledgeAdapter(KnowledgeRuntime())
    result = await service.send(USER_ID, _request(skill_slugs=['deep-research'], knowledge_base_ids=['kb-1']))
    [event async for event in result]
    sent = runtime.stream_calls[0][1]
    assert sent[0].role == 'system'
    assert '<selected_skills>' in sent[0].content
    assert 'You are a platform assistant.' in sent[0].content
    assert 'Private reference' not in sent[0].content
    assert sent[-2].role == 'user'
    assert 'Private reference' in sent[-2].content
    assert 'doc-1' in sent[-2].content
    assert sent[-1].content == 'hello'
    assert dao.messages[0].knowledge_base_ids == ['kb-1']
    assert dao.messages[0].skill_snapshots == [{'slug': 'deep-research', 'name': '深度研究'}]


@pytest.mark.asyncio
async def test_knowledge_failure_does_not_create_session_message_or_run() -> None:
    service, db, dao, runtime, _ = _service()
    with pytest.raises(PlatformChatError) as raised:
        await service.send(USER_ID, _request(knowledge_base_ids=['kb-1']))
    assert raised.value.code == 'CHAT_KNOWLEDGE_UNAVAILABLE'
    assert dao.session is None
    assert dao.messages == []
    assert dao.runs == []
    assert db.commit_count == 0
    assert runtime.stream_calls == []


@pytest.mark.asyncio
async def test_idempotency_rejects_changed_knowledge_selection() -> None:
    service, _, dao, runtime, _ = _service()
    result = await service.send(USER_ID, _request())
    [event async for event in result]

    class EmptyKnowledge:
        async def resolve(self, user_id: int, ids: list[str], query: str) -> tuple[ResolvedChatKnowledgePassage, ...]:
            return ()

    service._knowledge_resolver = EmptyKnowledge()
    with pytest.raises(PlatformChatError) as raised:
        await service.send(USER_ID, _request(session_id=SESSION_ID, knowledge_base_ids=['kb-1']))
    assert raised.value.code == 'CHAT_IDEMPOTENCY_CONFLICT'
    assert len(runtime.stream_calls) == 1
    assert len(dao.messages) == MESSAGE_COUNT


def test_knowledge_references_follow_history_and_precede_current_question() -> None:
    history = [AiChatMessage(role='user', content='Previous question', status='complete')]
    passages = (ResolvedChatKnowledgePassage('Relevant reference', 'doc-1', 'Guide', 'kb-1'),)
    messages = PlatformChatService._build_messages(
        history, 'Current question', 'Current question', None, None, passages
    )
    assert [message.role for message in messages] == ['system', 'user', 'user', 'user']
    assert messages[1].content == 'Previous question'
    assert 'Relevant reference' in messages[2].content
    assert messages[-1].content == 'Current question'
    assert 'Relevant reference' not in messages[0].content
