"""Populated schema15 retains every original field while adding native file purpose."""

from uuid import UUID

import httpx
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.core.integration.test_resource_requests import setup_access, upload
from tests.core.legacy_schema import reconstruct
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.infrastructure.database import SCHEMA_REVISION


async def test_schema15_original_rows_preserved_with_file_purpose(admin: httpx.AsyncClient, app: FastAPI) -> None:
    c, owner_app, _key, caller = await setup_access(admin, app)
    original = await upload(admin)
    async with c.database.sessions.begin() as session:
        await c.resource_access.bind(
            session, caller.principal, UUID(owner_app['id']), 'file', UUID(original['id']), ['read'], expected_version=1
        )
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def schema15(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, '0015_resource_api_access')

    async with c.database.engine.begin() as connection:
        await reconstruct(connection, schema15, preserve_personal_skills=True)
        columns = (
            await connection.execute(
                text(
                    "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND table_name!='alembic_version' ORDER BY table_name,ordinal_position"
                )
            )
        ).all()
        tables = sorted({r[0] for r in columns})
        before = {
            table: list(await connection.scalars(text(f'SELECT to_jsonb(t)::text FROM "{table}" t ORDER BY 1')))
            for table in tables
        }
        assert await connection.scalar(text('SELECT count(*) FROM files')) == 1
    await migrate(c.settings)
    async with c.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
        for table in tables:
            projection = ','.join('"' + column + '"' for name, column in columns if name == table)
            after = list(
                await connection.scalars(
                    text(f'SELECT to_jsonb(t)::text FROM (SELECT {projection} FROM "{table}") t ORDER BY 1')
                )
            )
            assert after == before[table], table
        assert await connection.scalar(text('SELECT purpose FROM files')) == 'general'
        assert (
            await connection.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), schema_metadata())
            )
            == []
        )
