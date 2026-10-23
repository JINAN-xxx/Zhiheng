from uuid import UUID, uuid4

import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import text


async def test_0002_upgrade_retains_existing_users_models_apps_keys_runs_and_ledger(
    admin: httpx.AsyncClient, app: object, model_id: str
) -> None:
    from zhiheng.bootstrap.migrations import migrate, migration_directory
    from zhiheng.worker.execution import execute_run

    application = (
        await admin.post('/api/developer/applications', json={'code': 'preserved', 'name': 'Preserved'})
    ).json()
    issued = await admin.post(
        f'/api/developer/applications/{application["id"]}/keys', json={'name': 'Existing', 'model_ids': [model_id]}
    )
    assert issued.status_code == 201
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    run = (
        await admin.post(
            f'/api/chat/conversations/{conversation["id"]}/runs',
            json={'model_id': model_id, 'message': 'Original', 'client_request_id': str(uuid4())},
        )
    ).json()
    await execute_run(app.state.container, UUID(run['id']))
    tables = (
        'identity_users',
        'model_connections',
        'model_connection_revisions',
        'models',
        'developer_applications',
        'developer_keys',
        'runtime_runs',
        'developer_accounts',
        'developer_invocations',
    )
    container = app.state.container
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: object) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0002_developer')

    async with container.database.engine.begin() as connection:
        await connection.run_sync(downgrade)
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0002_developer'
        snapshots = {
            table: (
                await connection.execute(
                    text(f"SELECT (to_jsonb(t)-'reasoning'-'billing_owner_id')::text FROM {table} t ORDER BY 1")
                )
            )
            .scalars()
            .all()
            for table in tables
        }
    await migrate(container.settings)
    async with container.database.engine.connect() as connection:
        for table in tables:
            assert (
                await connection.execute(
                    text(f"SELECT (to_jsonb(t)-'reasoning'-'billing_owner_id')::text FROM {table} t ORDER BY 1")
                )
            ).scalars().all() == snapshots[table]
        assert await connection.scalar(text('SELECT COUNT(*) FROM skills')) == 2
        assert await connection.scalar(text('SELECT skill_snapshots FROM chat_messages LIMIT 1')) == []
