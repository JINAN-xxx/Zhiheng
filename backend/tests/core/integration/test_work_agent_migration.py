from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.database import SCHEMA_REVISION
from zhiheng.modules.work.orm import WorkMessage
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.orm import Run
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


async def legacy_task(settings: Settings, *, active: bool = False) -> tuple[object, object, object]:
    configuration = Config()
    configuration.set_main_option('script_location', str(migration_directory()))

    def upgrade(connection: Connection) -> None:
        configuration.attributes['connection'] = connection
        command.upgrade(configuration, '0040_assistant_agents')

    engine = create_async_engine(settings.database_url.get_secret_value())
    owner_id, task_id, agent_id = uuid4(), uuid4(), uuid4()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(upgrade)
            await connection.execute(
                text("""INSERT INTO identity_users
                (id,username,password_hash,is_admin,is_active,display_name,email,phone,remark,version)
                VALUES (:id,'legacy-owner','unused',false,true,'','','','',1)"""),
                {'id': owner_id},
            )
            await connection.execute(
                text("""INSERT INTO work_tasks
                (id,owner_id,title,goal,status,version,active_agent_id,create_active_agent_id,
                 create_client_request_id,create_fingerprint,created_at,updated_at)
                VALUES (:id,:owner,'历史任务','保留历史成果','active',7,:agent,:agent,:request,'legacy-create',:now,:now)"""),
                {'id': task_id, 'owner': owner_id, 'agent': agent_id, 'request': uuid4(), 'now': utcnow()},
            )
        async with AsyncSession(engine) as session, session.begin():
            session.add(
                WorkMessage(task_id=task_id, client_request_id=uuid4(), sequence=1, role='user', content='原始要求')
            )
            session.add(
                WorkMessage(
                    task_id=task_id,
                    client_request_id=uuid4(),
                    sequence=2,
                    role='assistant',
                    content='历史结果',
                    agent_id=agent_id,
                    agent_name_snapshot='历史助手',
                    agent_revision=3,
                )
            )
            if active:
                session.add(
                    Run(
                        owner_id=owner_id,
                        billing_owner_id=owner_id,
                        resource_type='work_turn',
                        resource_id=task_id,
                        client_request_id=uuid4(),
                        fingerprint='active-legacy',
                        payload={},
                        status='pending',
                    )
                )
    finally:
        await engine.dispose()
    return owner_id, task_id, agent_id


async def test_schema40_upgrade_preserves_history_and_marks_tasks_legacy(settings: Settings) -> None:
    owner_id, task_id, agent_id = await legacy_task(settings)
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with AsyncSession(engine) as session:
            assert await session.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
            detail = await WorkService().detail(session, Principal(owner_id, 'legacy-owner', False), task_id)
            assert detail.id == task_id and detail.active_agent_id == agent_id
            assert detail.mode == 'legacy' and detail.version == 7 and detail.title == '历史任务'
            assert detail.configuration is None and detail.execution_state == 'idle'
            assert [message.content for message in detail.messages] == ['原始要求', '历史结果']
            assert detail.messages[1].agent_revision == 3
            assert await session.scalar(text("SELECT to_regclass('work_checkpoints.checkpoints') IS NOT NULL"))
            assert await session.scalar(text('SELECT count(*) FROM work_checkpoints.checkpoint_migrations')) == 10
    finally:
        await engine.dispose()


async def test_upgrade_rejects_active_legacy_work_and_leaves_schema40_intact(settings: Settings) -> None:
    await legacy_task(settings, active=True)
    with pytest.raises(RuntimeError, match='Finish or cancel'):
        await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0040_assistant_agents'
            assert await connection.scalar(text("SELECT count(*) FROM runtime_runs WHERE status='pending'")) == 1
    finally:
        await engine.dispose()
