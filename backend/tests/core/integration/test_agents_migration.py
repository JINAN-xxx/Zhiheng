from uuid import UUID, uuid4

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from tests.core.integration.test_developer import provision
from tests.core.integration.test_skills import install
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.worker.execution import execute_run


async def test_0003_upgrade_keeps_existing_columns_and_backfills_billing(
    admin: httpx.AsyncClient, app: object, model_id: str
) -> None:
    await provision(admin, model_id)
    await install(admin, 'before-agents')
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    response = await admin.post(
        f'/api/chat/conversations/{conversation["id"]}/runs',
        json={
            'model_id': model_id,
            'message': 'original',
            'skill_slugs': ['before-agents'],
            'client_request_id': str(uuid4()),
        },
    )
    await execute_run(app.state.container, UUID(response.json()['id']))
    container = app.state.container
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: object) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0003_skills')

    async with container.database.engine.begin() as connection:
        await connection.run_sync(downgrade)
        tables = (
            (
                await connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename!='alembic_version' ORDER BY tablename"
                    )
                )
            )
            .scalars()
            .all()
        )
        snapshots = {
            table: (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1')))
            .scalars()
            .all()
            for table in tables
        }
    await migrate(container.settings)
    async with container.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0005_work'
        for table in tables:
            # Compare every original value; new projection and billing columns are verified below.
            assert (
                await connection.execute(
                    text(f"SELECT (to_jsonb(t)-'reasoning'-'billing_owner_id')::text FROM {table} t ORDER BY 1")
                )
            ).scalars().all() == snapshots[table]
        assert (
            await connection.scalar(text('SELECT count(*) FROM runtime_runs WHERE billing_owner_id != owner_id')) == 0
        )
        assert await connection.scalar(text("SELECT count(*) FROM runtime_runs WHERE reasoning != ''")) == 0
        assert await connection.scalar(text("SELECT count(*) FROM chat_messages WHERE reasoning != ''")) == 0
        assert await connection.scalar(text('SELECT count(*) FROM skills')) == 3
