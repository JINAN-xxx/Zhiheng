"""The cleanup migration preserves history and refuses unowned pending work."""

from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.runtime.orm import Run


async def _upgrade_to(settings: Settings, revision: str) -> None:
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def upgrade(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, revision)

    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.begin() as connection:
            await connection.run_sync(upgrade)
    finally:
        await engine.dispose()


async def _legacy_summary(settings: Settings, status: str) -> tuple[UUID, UUID, UUID]:
    engine = create_async_engine(settings.database_url.get_secret_value())
    owner_id, conversation_id, task_id = uuid4(), uuid4(), uuid4()
    try:
        async with async_sessionmaker(engine).begin() as session:
            session.add(IdentityUser(id=owner_id, username='migration-owner', password_hash='not-a-login-hash'))
            await session.flush()
            session.add(Conversation(id=conversation_id, owner_id=owner_id, title='Legacy conversation'))
            await session.flush()
            await session.execute(
                text(
                    'INSERT INTO chat_summary_tasks '
                    '(id,conversation_id,owner_id,expected_version,covered_count,source_hash,source_refs,file_refs,'
                    'model_binding,status,summary,created_at) '
                    "VALUES (:id,:conversation,:owner,0,0,:hash,'[]'::jsonb,'[]'::jsonb,'{}'::jsonb,:status,"
                    "'legacy summary',now())"
                ),
                {'id': task_id, 'conversation': conversation_id, 'owner': owner_id, 'hash': 'a' * 64, 'status': status},
            )
        return owner_id, conversation_id, task_id
    finally:
        await engine.dispose()


async def test_cleanup_preserves_terminal_history_and_removes_manual_state(settings: Settings) -> None:
    await _upgrade_to(settings, '0035_chat_checkpoints')
    owner_id, conversation_id, task_id = await _legacy_summary(settings, 'published')
    context = {
        'model_id': str(uuid4()),
        'max_output_tokens': None,
        'temperature': 0.5,
        'system_prompt': 'retain the original settings',
        'skill_slugs': [],
        'file_ids': [],
        'knowledge_base_ids': [str(uuid4())],
    }
    message_id, run_id = uuid4(), uuid4()
    seed_engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with async_sessionmaker(seed_engine).begin() as session:
            session.add(
                Run(
                    id=run_id,
                    owner_id=owner_id,
                    billing_owner_id=owner_id,
                    resource_type='chat',
                    resource_id=conversation_id,
                    client_request_id=uuid4(),
                    fingerprint='b' * 64,
                    payload={},
                    status='completed',
                )
            )
            await session.flush()
            session.add(
                ChatMessage(
                    id=message_id,
                    conversation_id=conversation_id,
                    run_id=run_id,
                    role='user',
                    content='legacy question',
                    request_context=context,
                )
            )
    finally:
        await seed_engine.dispose()
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text('SELECT parent_run_id,parent_attempt_id,status,summary FROM chat_summary_tasks WHERE id=:id'),
                    {'id': task_id},
                )
            ).one()
            assert tuple(row) == (None, None, 'published', 'legacy summary')
            normalized = await connection.scalar(
                text('SELECT request_context FROM chat_messages WHERE id=:id'), {'id': message_id}
            )
            assert normalized == {key: value for key, value in context.items() if key != 'knowledge_base_ids'}
            assert not await connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='chat_messages' AND column_name='knowledge_sources')"
                )
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT data_type FROM information_schema.columns WHERE table_schema='public' "
                        "AND table_name='chat_context_states' AND column_name='checkpoint_cleanup_after'"
                    )
                )
                == 'timestamp with time zone'
            )

            columns = set(
                (
                    await connection.execute(
                        text(
                            "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' "
                            "AND table_name IN ('chat_context_states','chat_summary_tasks','chat_context_snapshots')"
                        )
                    )
                ).tuples()
            )
            removed = {
                ('chat_context_states', 'target_covered_count'),
                ('chat_context_states', 'dirty'),
                ('chat_context_states', 'summary_task_id'),
                ('chat_context_states', 'last_run_id'),
                ('chat_summary_tasks', 'previous_task_id'),
                ('chat_summary_tasks', 'retry_request_id'),
                ('chat_context_snapshots', 'summary_task_id'),
            }
            assert not columns & removed
            indexes = set(
                await connection.scalars(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname='public' AND indexname IN "
                        "('ix_chat_summary_tasks_parent_run_id','ix_chat_context_snapshots_checkpoint')"
                    )
                )
            )
            assert indexes == {'ix_chat_summary_tasks_parent_run_id', 'ix_chat_context_snapshots_checkpoint'}
    finally:
        await engine.dispose()


async def test_cleanup_refuses_legacy_pending_summary_without_inventing_parent(settings: Settings) -> None:
    await _upgrade_to(settings, '0035_chat_checkpoints')
    _, _, task_id = await _legacy_summary(settings, 'pending')
    with pytest.raises(RuntimeError, match='Pending chat summaries must finish or be cancelled'):
        await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0035_chat_checkpoints'
            assert (
                await connection.scalar(text('SELECT status FROM chat_summary_tasks WHERE id=:id'), {'id': task_id})
                == 'pending'
            )
            assert not await connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='chat_summary_tasks' AND column_name='parent_run_id')"
                )
            )
    finally:
        await engine.dispose()


async def test_cleanup_database_rejects_pending_summary_without_parent_identity(settings: Settings) -> None:
    await _upgrade_to(settings, '0035_chat_checkpoints')
    _, _, task_id = await _legacy_summary(settings, 'failed')
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        with pytest.raises(IntegrityError, match='ck_chat_summary_pending_parent'):
            async with engine.begin() as connection:
                await connection.execute(
                    text("UPDATE chat_summary_tasks SET status='pending' WHERE id=:id"), {'id': task_id}
                )
        with pytest.raises(IntegrityError, match='ck_chat_summary_parent_identity'):
            async with engine.begin() as connection:
                await connection.execute(
                    text('UPDATE chat_summary_tasks SET parent_attempt_id=:attempt WHERE id=:id'),
                    {'id': task_id, 'attempt': uuid4()},
                )
    finally:
        await engine.dispose()
