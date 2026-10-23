"""Deletion must observe active runs after an old read or a concurrent sender."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from plugins.ai.dao.platform_chat_dao import PlatformChatDao
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.do.ai_chat_platform_do import AiChatMessage, AiChatPreferences, AiChatRun, AiChatSession
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.service.chat_runtime_coordinator import ChatCoordinationError
from tests.plugins.ai.test_model_reference_concurrency import wait_for_database_lock
from tests.plugins.ai.test_platform_chat_service import BINDING_ID, MODEL_ID, SESSION_ID, USER_ID, _archive_service

POSTGRES_DSN = os.getenv('ZHIHENG_TEST_POSTGRES_DSN')
pytestmark = pytest.mark.skipif(
    not POSTGRES_DSN, reason='Set ZHIHENG_TEST_POSTGRES_DSN to a disposable PostgreSQL database'
)
RUN_COUNT = 2


@pytest_asyncio.fixture
async def postgres_chat() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    schema = 'test_chat_lifecycle_' + uuid4().hex
    control = create_async_engine(POSTGRES_DSN)
    async with control.begin() as connection:
        await connection.execute(CreateSchema(schema))
    engine = create_async_engine(POSTGRES_DSN, connect_args={'server_settings': {'search_path': schema}})
    try:
        async with engine.begin() as connection:
            for table in (
                AiModelCatalog.__table__,
                AiResourceBinding.__table__,
                AiChatSession.__table__,
                AiChatMessage.__table__,
                AiChatRun.__table__,
                AiChatPreferences.__table__,
            ):
                await connection.run_sync(table.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add(
                AiModelCatalog(
                    model_id=MODEL_ID,
                    model_code='chat-concurrency',
                    display_name='Chat concurrency',
                    model_type='chat',
                    lifecycle_status='active',
                )
            )
            await db.flush()
            db.add(
                AiResourceBinding(
                    binding_id=BINDING_ID,
                    resource_type='model',
                    resource_id=MODEL_ID,
                    engine_type='litellm',
                    engine_resource_id='test-runtime',
                    binding_status='active',
                )
            )
            db.add(
                AiChatSession(session_id=SESSION_ID, user_id=USER_ID, model_id=MODEL_ID, title='keep', status='active')
            )
            await db.commit()
        yield sessions
    finally:
        await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(DropSchema(schema, cascade=True))
        await control.dispose()


async def add_active_and_newer_failed_runs(db: AsyncSession) -> None:
    now = datetime.now()
    for offset, status in enumerate(('running', 'failed')):
        db.add(
            AiChatRun(
                run_id=new_resource_id('run'),
                session_id=SESSION_ID,
                model_id=MODEL_ID,
                binding_id=BINDING_ID,
                engine_type='litellm',
                engine_model_snapshot='test-runtime',
                status=status,
                created_at=now + timedelta(seconds=offset),
                updated_at=now + timedelta(seconds=offset),
            )
        )
    await db.flush()


@pytest.mark.asyncio
async def test_delete_discards_an_existing_stale_read_snapshot(
    postgres_chat: async_sessionmaker[AsyncSession],
) -> None:
    async with postgres_chat() as deletion, postgres_chat() as writer:
        # Only this existing transaction has a stale snapshot. The transaction
        # used after rollback returns to the platform's READ COMMITTED default.
        await deletion.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
        assert not await PlatformChatDao.has_active_run(deletion, SESSION_ID)
        await add_active_and_newer_failed_runs(writer)
        await writer.commit()
        assert not await PlatformChatDao.has_active_run(deletion, SESSION_ID)

        service, _ = _archive_service(deletion)
        with pytest.raises(ChatCoordinationError) as raised:
            await service.delete_session(USER_ID, SESSION_ID)

        assert raised.value.code == 'CHAT_SESSION_BUSY'
        assert not deletion.in_transaction()
        assert await deletion.get(AiChatSession, SESSION_ID) is not None
        assert len((await deletion.execute(select(AiChatRun))).scalars().all()) == RUN_COUNT


@pytest.mark.asyncio
async def test_delete_waits_for_sender_and_checks_all_committed_runs(
    postgres_chat: async_sessionmaker[AsyncSession],
) -> None:
    # Bind the deletion session to a checked-out connection so rollback does not
    # change the PID used to observe its actual PostgreSQL row-lock wait.
    async with (
        postgres_chat.kw['bind'].connect() as connection,
        AsyncSession(bind=connection) as deletion,
        postgres_chat() as writer,
    ):
        assert await deletion.scalar(text('SHOW transaction_isolation')) == 'read committed'
        pid = await deletion.scalar(text('SELECT pg_backend_pid()'))
        assert await PlatformChatDao.get_owned_session_for_update(writer, SESSION_ID, user_id=USER_ID)
        await add_active_and_newer_failed_runs(writer)
        service, _ = _archive_service(deletion)
        pending = asyncio.create_task(service.delete_session(USER_ID, SESSION_ID))
        try:
            await wait_for_database_lock(postgres_chat, pid)
            await writer.commit()
            with pytest.raises(ChatCoordinationError) as raised:
                await asyncio.wait_for(pending, 5)
            assert raised.value.code == 'CHAT_SESSION_BUSY'
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    async with postgres_chat() as db:
        assert await db.get(AiChatSession, SESSION_ID) is not None
        assert len((await db.execute(select(AiChatRun))).scalars().all()) == RUN_COUNT
