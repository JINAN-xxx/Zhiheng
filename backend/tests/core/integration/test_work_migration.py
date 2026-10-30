from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text

from tests.core.integration.test_agents import config, provision
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.infrastructure.database import SCHEMA_REVISION


async def test_0004_upgrade_preserves_data_and_matches_metadata(admin: object, model_id: str, app: object) -> None:
    application, _ = await provision(admin, model_id)
    agent = (await admin.post('/api/agents', json=config(model_id, application_id=application))).json()
    await admin.post('/api/agents/' + agent['id'] + '/publish', json={'expected_version': 1})
    container = app.state.container
    config_ = Config()
    config_.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: object) -> None:
        config_.attributes['connection'] = connection
        command.downgrade(config_, '0004_agents')

    async with container.database.engine.begin() as connection:
        await connection.run_sync(downgrade)
        names = (
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
        original_columns = {
            name: ','.join(
                f'"{column}"'
                for column in await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table ORDER BY ordinal_position"
                    ),
                    {'table': name},
                )
            )
            for name in names
        }
        snapshots = {
            name: (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {name} t ORDER BY 1'))).scalars().all()
            for name in names
        }
    await migrate(container.settings)

    def compare(connection: object) -> list:
        return compare_metadata(MigrationContext.configure(connection), schema_metadata())

    async with container.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
        for name in names:
            assert (
                await connection.execute(
                    text(f'SELECT to_jsonb(t)::text FROM (SELECT {original_columns[name]} FROM {name}) t ORDER BY 1')
                )
            ).scalars().all() == snapshots[name]
        assert await connection.run_sync(compare) == []
