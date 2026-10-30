from typing import Any

import httpx
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection

from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.infrastructure.database import SCHEMA_REVISION


async def test_schema5_rows_and_column_types_preserved(app: FastAPI, model_id: str, admin: httpx.AsyncClient) -> None:
    container = app.state.container
    await admin.post('/api/chat/conversations', json={})
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0005_work')

    async with container.database.engine.begin() as connection:
        await connection.run_sync(downgrade)
        columns = (
            await connection.execute(
                text(
                    "SELECT table_name,column_name,udt_name,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name!='alembic_version' ORDER BY table_name,ordinal_position"
                )
            )
        ).all()
        tables = sorted({r[0] for r in columns})
        snapshots = {
            name: (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {name} t ORDER BY 1'))).scalars().all()
            for name in tables
        }
    await migrate(container.settings)
    async with container.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
        for name in tables:
            old = [r[1] for r in columns if r[0] == name]
            assert (
                await connection.execute(
                    text(f'SELECT to_jsonb(t)::text FROM (SELECT {",".join(old)} FROM {name}) t ORDER BY 1')
                )
            ).scalars().all() == snapshots[name]
        updated = (
            await connection.execute(
                text(
                    "SELECT table_name,column_name,udt_name,is_nullable FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position"
                )
            )
        ).all()
        # 0012 intentionally widens token counters; every value and nullability
        # still survives, and all other existing column types remain unchanged.
        widened = {
            ('developer_accounts', 'limit_tokens'),
            ('developer_accounts', 'used_tokens'),
            ('developer_accounts', 'reserved_tokens'),
            ('developer_minute_buckets', 'tokens'),
        }
        for table, name, old_type, nullable in columns:
            expected_type = 'int8' if (table, name) in widened else old_type
            assert (table, name, expected_type, nullable) in updated

        def compare(sync: Connection) -> list[Any]:
            return compare_metadata(MigrationContext.configure(sync), schema_metadata())

        assert await connection.run_sync(compare) == []
