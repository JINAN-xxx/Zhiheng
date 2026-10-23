from collections.abc import AsyncIterator
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.do.work_task_do import AgentWorkMessage, AgentWorkTask, AgentWorkTaskMember
from plugins.agent_extension.public.model_references import has_model_references

MODEL = 'mdl_referenced'
OTHER = 'mdl_other'


@pytest_asyncio.fixture
async def reference_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for table in [
            AgentConfiguration.__table__,
            AgentWorkTask.__table__,
            AgentWorkTaskMember.__table__,
            AgentWorkMessage.__table__,
        ]:
            await connection.run_sync(table.create)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['draft', 'published-camel', 'published-snake', 'removed-member', 'audit'])
async def test_agent_and_retained_work_snapshots_protect_the_referenced_model(
    reference_db: async_sessionmaker[AsyncSession], kind: str
) -> None:
    async with reference_db() as db:
        if kind.startswith('published') or kind == 'draft':
            db.add(
                AgentConfiguration(
                    agent_id='agent',
                    owner_user_id=1,
                    name='Agent',
                    model_id=MODEL if kind == 'draft' else OTHER,
                    published_config={('model_id' if kind.endswith('snake') else 'modelId'): MODEL}
                    if kind != 'draft'
                    else None,
                )
            )
        else:
            db.add(
                AgentWorkTask(
                    task_id='task',
                    owner_user_id=1,
                    create_client_message_id='create',
                    title='Task',
                    goal='Goal',
                    status='archived',
                )
            )
            await db.flush()
            if kind == 'removed-member':
                db.add(
                    AgentWorkTaskMember(
                        task_id='task',
                        agent_id='agent',
                        agent_name_snapshot='Agent',
                        published_version=1,
                        config_snapshot={'modelId': MODEL},
                        removed_at=datetime.now(),
                    )
                )
            else:
                db.add(
                    AgentWorkMessage(
                        message_id='message',
                        task_id='task',
                        role='assistant',
                        content='Completed result',
                        agent_id='agent',
                        agent_name_snapshot='Agent',
                        published_version=1,
                        run_id='run',
                        run_status='completed',
                        reply_to_message_id='user',
                        audit_snapshot={'modelId': MODEL},
                    )
                )
        await db.commit()
        assert await has_model_references(db, MODEL)
        assert not await has_model_references(db, 'unrelated')
        assert db.in_transaction(), 'Query must not close the deletion transaction'


@pytest.mark.asyncio
async def test_empty_reference_query_leaves_callers_write_uncommitted(
    reference_db: async_sessionmaker[AsyncSession],
) -> None:
    async with reference_db() as db:
        db.add(AgentConfiguration(agent_id='pending', owner_user_id=1, name='Agent', model_id=OTHER))
        await db.flush()
        assert not await has_model_references(db, MODEL)
        await db.rollback()
    async with reference_db() as db:
        assert await db.get(AgentConfiguration, 'pending') is None
