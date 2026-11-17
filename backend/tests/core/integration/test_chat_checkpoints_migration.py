"""Real PostgreSQL contracts for the official ordinary-chat checkpointer."""

from uuid import uuid4

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.agents.adapters.checkpoints import CheckpointStore
from zhiheng.shared.errors import AppError

CHECKPOINT_VERSION = 9
BUSINESS_COLUMNS = {
    ('chat_context_states', 'agent_checkpoint_id', 'text', 'YES'),
    ('chat_context_states', 'agent_source_hash', 'character varying', 'YES'),
    ('chat_context_snapshots', 'agent_context', 'jsonb', 'YES'),
    ('chat_context_snapshots', 'agent_checkpoint_id', 'text', 'YES'),
}


async def _catalog(engine: AsyncEngine, schema: str) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    async with engine.connect() as connection:
        columns = await connection.execute(
            text(
                'SELECT table_name,column_name,data_type,is_nullable,column_default '
                'FROM information_schema.columns WHERE table_schema=:schema ORDER BY table_name,ordinal_position'
            ),
            {'schema': schema},
        )
        indexes = await connection.execute(
            text(
                "SELECT indexname,replace(indexdef,:schema,'SCHEMA') FROM pg_indexes "
                'WHERE schemaname=:schema ORDER BY indexname'
            ),
            {'schema': schema},
        )
        return [tuple(row) for row in columns], [tuple(row) for row in indexes]


async def test_chat_checkpoint_runtime_requires_explicit_migration(settings: Settings) -> None:
    store = CheckpointStore(settings.database_url.get_secret_value())
    with pytest.raises(AppError, match='对话检查点尚未初始化') as rejected:
        async with store.saver():
            pytest.fail('Runtime saver must refuse an uninitialized checkpoint schema')
    assert rejected.value.code == 'chat_checkpoint_schema_unavailable'
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT to_regnamespace('chat_checkpoints')")) is None
    finally:
        await engine.dispose()


async def test_chat_checkpoint_migration_matches_official_setup(settings: Settings) -> None:
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.begin() as connection:
            await connection.execute(text('CREATE SCHEMA checkpoint_reference'))
        conninfo = make_url(settings.database_url.get_secret_value()).set(drivername='postgresql')
        async with await AsyncConnection.connect(
            conninfo.render_as_string(hide_password=False),
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
            options='-csearch_path=checkpoint_reference,pg_catalog',
        ) as connection:
            await AsyncPostgresSaver(connection).setup()
        assert await _catalog(engine, 'chat_checkpoints') == await _catalog(engine, 'checkpoint_reference')
        async with engine.connect() as connection:
            versions = await connection.scalars(text('SELECT v FROM chat_checkpoints.checkpoint_migrations ORDER BY v'))
            assert list(versions) == list(range(CHECKPOINT_VERSION + 1))
            columns = await connection.execute(
                text(
                    'SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns '
                    "WHERE table_schema='public' AND column_name LIKE 'agent_%' "
                    "AND table_name IN ('chat_context_states','chat_context_snapshots')"
                )
            )
            assert {tuple(row) for row in columns} == BUSINESS_COLUMNS
    finally:
        await engine.dispose()


async def test_chat_checkpoint_root_and_accepted_head_ignore_newer_branch(settings: Settings) -> None:
    await migrate(settings)
    store = CheckpointStore(settings.database_url.get_secret_value())
    conversation_id = uuid4()
    async with store.saver() as saver:
        root = store.config(conversation_id)
        assert await saver.aget_tuple(root) is None
        # Distinct response objects model distinct calls: reusing one message ID
        # would deliberately replace that message under LangGraph's reducer.
        model = FakeMessagesListChatModel(responses=[AIMessage(content='answer') for _ in range(3)])
        agent = create_agent(model, tools=[], checkpointer=saver)
        checkpoints = [
            checkpoint
            async for checkpoint in agent.astream(
                {'messages': [HumanMessage(content='accepted')]}, root, stream_mode='checkpoints'
            )
        ]
        accepted_id = store.result_checkpoint_id(checkpoints[-1]['config'])
        assert [message.content for message in checkpoints[-1]['values']['messages']] == ['accepted', 'answer']

        # A newer physical branch must neither become the effective head nor
        # contaminate a new root after a failed/cancelled business execution.
        checkpoints = [
            checkpoint
            async for checkpoint in agent.astream(
                {'messages': [HumanMessage(content='orphan')]}, root, stream_mode='checkpoints'
            )
        ]
        assert [message.content for message in checkpoints[-1]['values']['messages']] == ['orphan', 'answer']
        assert store.result_checkpoint_id(checkpoints[-1]['config']) != accepted_id
        assert await saver.aget_tuple(root) is None

        checkpoints = [
            checkpoint
            async for checkpoint in agent.astream(
                {'messages': [HumanMessage(content='followup')]},
                store.config(conversation_id, accepted_id),
                stream_mode='checkpoints',
            )
        ]
        assert [message.content for message in checkpoints[-1]['values']['messages']] == [
            'accepted',
            'answer',
            'followup',
            'answer',
        ]
        assert await saver.aget_tuple(store.config(uuid4(), accepted_id)) is None
