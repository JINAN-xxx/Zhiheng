from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from sqlalchemy import text

from tests.core.integration.test_agents import config, provision
from tests.core.legacy_schema import reconstruct
from tests.core.schema_support import active_migration_context, assert_original_rows
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.infrastructure.database import SCHEMA_REVISION


async def test_0004_upgrade_preserves_data_and_matches_metadata(admin: object, model_id: str, app: object) -> None:
    application, _ = await provision(admin, model_id)
    agent = (await admin.post('/api/agents', json=config(model_id, application_id=application))).json()
    await admin.post('/api/agents/' + agent['id'] + '/enable', json={'expected_version': 1})
    container = app.state.container
    config_ = Config()
    config_.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: object) -> None:
        config_.attributes['connection'] = connection
        command.upgrade(config_, '0004_agents')

    async with container.database.engine.begin() as connection:
        await reconstruct(connection, downgrade)
        names = (
            (
                await connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT IN ('alembic_version','agent_versions') ORDER BY tablename"
                    )
                )
            )
            .scalars()
            .all()
        )
        snapshots = {
            name: (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {name} t ORDER BY 1'))).scalars().all()
            for name in names
        }
    await migrate(container.settings)

    def compare(connection: object) -> list:
        return compare_metadata(active_migration_context(connection), schema_metadata())

    async with container.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
        for name in names:
            rows = (
                (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {name} t ORDER BY 1'))).scalars().all()
            )
            assert_original_rows(snapshots[name], rows, name)
        assert await connection.run_sync(compare) == []
