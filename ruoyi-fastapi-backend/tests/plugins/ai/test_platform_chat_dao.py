from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plugins.ai.dao.platform_chat_dao import PlatformChatDao
from plugins.ai.entity.do.ai_chat_platform_do import AiChatMessage, AiChatRun, AiChatSession
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.entity.vo.ai_platform_chat_vo import (
    CancelRunRequestModel,
    PlatformChatRequestModel,
    PlatformImageReferenceModel,
    PlatformRunModel,
    PlatformSessionPageQueryModel,
)

MODEL_ID = 'mdl_00000000000040008000000000000001'
BINDING_ID = 'bnd_00000000000040008000000000000001'
SESSION_ID = 'ses_00000000000040008000000000000001'
OTHER_SESSION_ID = 'ses_00000000000040008000000000000002'
MESSAGE_ID = 'msg_00000000000040008000000000000001'
RUN_ID = 'run_00000000000040008000000000000001'
NOW = datetime(2026, 8, 17, 12, 0, 0)
OWNED_SESSION_COUNT = 2


@pytest_asyncio.fixture
async def session_maker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for table in (
            AiModelCatalog.__table__,
            AiResourceBinding.__table__,
            AiChatSession.__table__,
            AiChatMessage.__table__,
            AiChatRun.__table__,
        ):
            await connection.run_sync(table.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


async def _seed_model_and_binding(session: AsyncSession) -> None:
    session.add(
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
    session.add(
        AiResourceBinding(
            binding_id=BINDING_ID,
            resource_type='model',
            resource_id=MODEL_ID,
            engine_type='litellm',
            engine_resource_id='chat-alias',
            binding_status='active',
            engine_metadata={},
            version=1,
            create_by='tester',
            create_time=NOW,
            update_by='tester',
            update_time=NOW,
        )
    )
    await session.flush()


def _session(session_id: str, user_id: int, *, updated_at: datetime) -> AiChatSession:
    return AiChatSession(
        session_id=session_id,
        user_id=user_id,
        model_id=MODEL_ID,
        title=session_id,
        status='active',
        created_at=updated_at,
        updated_at=updated_at,
    )


def _message(
    message_id: str,
    client_message_id: str,
    *,
    role: str,
    status: str,
    content: str,
    created_at: datetime,
) -> AiChatMessage:
    return AiChatMessage(
        message_id=message_id,
        session_id=SESSION_ID,
        client_message_id=client_message_id,
        role=role,
        content=content,
        reasoning_content=None,
        images=[],
        status=status,
        created_at=created_at,
        updated_at=created_at,
    )


def _run() -> AiChatRun:
    return AiChatRun(
        run_id=RUN_ID,
        session_id=SESSION_ID,
        model_id=MODEL_ID,
        binding_id=BINDING_ID,
        engine_type='litellm',
        engine_model_snapshot='chat-alias',
        status='pending',
        created_at=NOW,
        updated_at=NOW,
    )


def test_platform_chat_request_accepts_only_platform_ids_and_file_references() -> None:
    request = PlatformChatRequestModel(
        modelId=MODEL_ID,
        sessionId=SESSION_ID,
        clientMessageId='browser-message-1',
        message='你好',
        images=[{'fileId': '00000000-0000-4000-8000-000000000001'}],
    )

    assert request.model_id == MODEL_ID
    assert request.session_id == SESSION_ID
    assert request.images == [PlatformImageReferenceModel(fileId='00000000-0000-4000-8000-000000000001')]
    assert '你好' not in repr(request)
    assert request.model_dump(by_alias=True)['message'] == '你好'

    invalid_images = (
        {'fileId': '/tmp/private.png'},
        {'fileId': 'file:///tmp/private.png'},
        {'fileId': 'https://example.com/private.png'},
        {'fileId': 'data:image/png;base64,AAAA'},
        {'fileId': '00000000-0000-4000-8000-000000000001', 'url': 'https://example.com'},
    )
    for image in invalid_images:
        with pytest.raises(ValidationError):
            PlatformChatRequestModel(
                modelId=MODEL_ID,
                clientMessageId='browser-message-1',
                message='你好',
                images=[image],
            )


@pytest.mark.parametrize(
    ('model', 'payload'),
    [
        (PlatformChatRequestModel, {'modelId': 1, 'clientMessageId': 'client-1', 'message': 'hi'}),
        (PlatformChatRequestModel, {'modelId': MODEL_ID, 'clientMessageId': '', 'message': 'hi'}),
        (PlatformChatRequestModel, {'modelId': MODEL_ID, 'clientMessageId': 'client-1', 'message': ''}),
        (CancelRunRequestModel, {'runId': SESSION_ID}),
        (PlatformSessionPageQueryModel, {'pageNum': 0}),
        (PlatformSessionPageQueryModel, {'pageSize': 101}),
    ],
)
def test_platform_chat_requests_reject_invalid_types_ranges_and_resource_prefixes(model: type, payload: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_platform_chat_requests_require_camel_case_and_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PlatformChatRequestModel(
            model_id=MODEL_ID,
            client_message_id='client-1',
            message='hi',
        )
    with pytest.raises(ValidationError):
        PlatformChatRequestModel(
            modelId=MODEL_ID,
            clientMessageId='client-1',
            message='hi',
            apiKey='must-not-be-accepted',
        )


def test_platform_run_response_rejects_non_binding_resource_id() -> None:
    with pytest.raises(ValidationError):
        PlatformRunModel.model_validate(
            {
                'runId': RUN_ID,
                'sessionId': SESSION_ID,
                'modelId': MODEL_ID,
                'bindingId': SESSION_ID,
                'engineType': 'litellm',
                'engineModelSnapshot': 'chat-alias',
                'status': 'pending',
                'createdAt': NOW,
                'updatedAt': NOW,
            }
        )


@pytest.mark.asyncio
async def test_platform_chat_dao_lists_only_owned_sessions_in_recent_order(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        await _seed_model_and_binding(session)
        session.add_all(
            [
                _session(SESSION_ID, 7, updated_at=NOW),
                _session(OTHER_SESSION_ID, 7, updated_at=NOW + timedelta(minutes=1)),
                _session('ses_00000000000040008000000000000003', 8, updated_at=NOW + timedelta(minutes=2)),
            ]
        )
        await session.commit()

        first_page = await PlatformChatDao.list_sessions(session, user_id=7, page_num=1, page_size=1)
        second_page = await PlatformChatDao.list_sessions(session, user_id=7, page_num=2, page_size=1)

    assert first_page.total == OWNED_SESSION_COUNT
    assert first_page.has_next is True
    assert first_page.rows[0]['sessionId'] == OTHER_SESSION_ID
    assert second_page.rows[0]['sessionId'] == SESSION_ID


@pytest.mark.asyncio
async def test_platform_chat_dao_enforces_session_ownership_and_model_lock(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        await _seed_model_and_binding(session)
        await PlatformChatDao.create_session(session, _session(SESSION_ID, 7, updated_at=NOW))

        assert await PlatformChatDao.get_owned_session(session, SESSION_ID, user_id=7) is not None
        assert await PlatformChatDao.get_owned_session(session, SESSION_ID, user_id=8) is None
        with pytest.raises(ValueError, match='model_id'):
            await PlatformChatDao.update_session(session, SESSION_ID, user_id=7, values={'model_id': 'other'})
        assert (
            await PlatformChatDao.update_session(
                session,
                SESSION_ID,
                user_id=7,
                values={'title': '新标题', 'updated_at': NOW + timedelta(minutes=1)},
            )
            == 1
        )


@pytest.mark.asyncio
async def test_platform_chat_message_client_id_is_idempotent_per_session(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        await _seed_model_and_binding(session)
        await PlatformChatDao.create_session(session, _session(SESSION_ID, 7, updated_at=NOW))
        first = _message(
            MESSAGE_ID,
            'browser-message-1',
            role='user',
            status='complete',
            content='first',
            created_at=NOW,
        )
        await PlatformChatDao.create_message(session, first)
        found = await PlatformChatDao.get_message_by_client_id(session, SESSION_ID, 'browser-message-1')

        assert found is first
        duplicate = _message(
            'msg_00000000000040008000000000000002',
            'browser-message-1',
            role='user',
            status='complete',
            content='duplicate',
            created_at=NOW,
        )
        with pytest.raises(IntegrityError):
            await PlatformChatDao.create_message(session, duplicate)


@pytest.mark.asyncio
async def test_platform_chat_history_returns_latest_complete_messages_in_chronological_order(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        await _seed_model_and_binding(session)
        await PlatformChatDao.create_session(session, _session(SESSION_ID, 7, updated_at=NOW))
        rows = [
            ('01', 'c1', 'user', 'complete', 'old user'),
            ('02', 'c2', 'assistant', 'interrupted', 'partial assistant'),
            ('03', 'c3', 'user', 'complete', 'new user'),
            ('04', 'c4', 'assistant', 'complete', 'new assistant'),
            ('05', 'c5', 'assistant', 'cancelled', 'cancelled assistant'),
        ]
        for index, (suffix, client_id, role, status, content) in enumerate(rows):
            await PlatformChatDao.create_message(
                session,
                _message(
                    f'msg_000000000000400080000000000000{suffix}',
                    client_id,
                    role=role,
                    status=status,
                    content=content,
                    created_at=NOW + timedelta(seconds=index),
                ),
            )

        history = await PlatformChatDao.list_history_messages(session, SESSION_ID, limit=2)

    assert [(message.role, message.content) for message in history] == [
        ('user', 'new user'),
        ('assistant', 'new assistant'),
    ]


@pytest.mark.asyncio
async def test_platform_chat_run_transition_is_conditional_and_terminal_safe(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        await _seed_model_and_binding(session)
        await PlatformChatDao.create_session(session, _session(SESSION_ID, 7, updated_at=NOW))
        await PlatformChatDao.create_run(session, _run())

        assert (
            await PlatformChatDao.transition_run_status(
                session,
                RUN_ID,
                expected_statuses={'pending'},
                values={'status': 'running', 'started_at': NOW, 'updated_at': NOW},
            )
            == 1
        )
        assert (
            await PlatformChatDao.transition_run_status(
                session,
                RUN_ID,
                expected_statuses={'pending'},
                values={'status': 'failed', 'updated_at': NOW},
            )
            == 0
        )
        assert (
            await PlatformChatDao.transition_run_status(
                session,
                RUN_ID,
                expected_statuses={'running'},
                values={'status': 'completed', 'finished_at': NOW, 'updated_at': NOW},
            )
            == 1
        )
        assert (
            await PlatformChatDao.transition_run_status(
                session,
                RUN_ID,
                expected_statuses={'running'},
                values={'status': 'failed', 'updated_at': NOW},
            )
            == 0
        )


@pytest.mark.asyncio
async def test_platform_chat_run_lookup_checks_session_owner(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    async with session_maker() as session:
        await _seed_model_and_binding(session)
        await PlatformChatDao.create_session(session, _session(SESSION_ID, 7, updated_at=NOW))
        await PlatformChatDao.create_run(session, _run())

        assert await PlatformChatDao.get_owned_run(session, RUN_ID, user_id=7) is not None
        assert await PlatformChatDao.get_owned_run(session, RUN_ID, user_id=8) is None
