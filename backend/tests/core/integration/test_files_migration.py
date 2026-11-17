from typing import Any

import httpx
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.core.legacy_schema import reconstruct
from tests.core.schema_support import (
    RENAMED_COLUMNS,
    RETIRED_COLUMNS,
    RETIRED_TABLES,
    active_migration_context,
    assert_original_rows,
)
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
        command.upgrade(config, '0005_work')

    async with container.database.engine.begin() as connection:
        await reconstruct(connection, downgrade)
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
            if name in RETIRED_TABLES:
                assert await connection.scalar(text('SELECT to_regclass(:table)'), {'table': name}) is None
                continue
            current = (
                (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {name} t ORDER BY 1'))).scalars().all()
            )
            assert_original_rows(snapshots[name], current, name)
        updated = (
            await connection.execute(
                text(
                    "SELECT table_name,column_name,udt_name,is_nullable FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position"
                )
            )
        ).all()
        # 0012 widens token counters; 0036/0039 retire fields and 0040 retires
        # the assistant publication catalog after preserving accepted audits.
        # Every remaining value, type and nullability must survive.
        widened = {
            ('models', 'max_output_tokens'),
            ('developer_accounts', 'limit_tokens'),
            ('developer_accounts', 'used_tokens'),
            ('developer_accounts', 'reserved_tokens'),
            ('developer_minute_buckets', 'tokens'),
        }
        for table, name, old_type, nullable in columns:
            if table in RETIRED_TABLES:
                assert not any(row[0] == table for row in updated)
                continue
            if name in RETIRED_COLUMNS.get(table, frozenset()):
                assert not any(row[0] == table and row[1] == name for row in updated)
                continue
            expected_type = 'int8' if (table, name) in widened else old_type
            current_name = RENAMED_COLUMNS.get(table, {}).get(name, name)
            # Agent-mode tasks no longer require a legacy assistant member;
            # their historical accepted member identifiers still survive.
            expected_nullable = 'YES' if (table, name) == ('work_tasks', 'create_active_agent_id') else nullable
            assert (table, current_name, expected_type, expected_nullable) in updated
            if current_name != name:
                assert not any(row[0] == table and row[1] == name for row in updated)

        def compare(sync: Connection) -> list[Any]:
            return compare_metadata(active_migration_context(sync), schema_metadata())

        assert await connection.run_sync(compare) == []
