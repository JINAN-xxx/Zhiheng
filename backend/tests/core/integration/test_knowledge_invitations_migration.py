"""Invitation schema changes preserve every pre-existing row and column."""

from typing import Any
from uuid import UUID

import httpx
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.provider import ProviderServer
from tests.core.schema_support import active_migration_context
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.modules.knowledge.orm import KnowledgeGrant


async def test_invitation_upgrade_preserves_existing_installation(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    container = app.state.container
    async with container.database.sessions.begin() as session:
        member = await session.scalar(text("SELECT id FROM identity_users WHERE username='member'"))
        session.add(KnowledgeGrant(base_id=UUID(base['id']), user_id=member, effect='allow'))
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def previous(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0021_knowledge_settings')

    async with container.database.engine.begin() as connection:
        await connection.run_sync(previous)
        tables = list(
            await connection.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename!='alembic_version' ORDER BY tablename"
                )
            )
        )
        columns = {
            table: list(
                await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table ORDER BY ordinal_position"
                    ),
                    {'table': table},
                )
            )
            for table in tables
        }
        before = {}
        for table in tables:
            before[table] = list(
                await connection.scalars(
                    text('SELECT to_jsonb(t)::text FROM "' + table + '" t ORDER BY to_jsonb(t)::text')
                )
            )
    await migrate(container.settings)
    async with container.database.engine.connect() as connection:
        for table in tables:
            projection = ','.join('"' + column + '"' for column in columns[table])
            after = list(
                await connection.scalars(
                    text(
                        'SELECT to_jsonb(t)::text FROM (SELECT '
                        + projection
                        + ' FROM "'
                        + table
                        + '") t ORDER BY to_jsonb(t)::text'
                    )
                )
            )
            assert before[table] == after, table
        assert await connection.scalar(text('SELECT count(*) FROM knowledge_invitations')) == 0
        assert await connection.scalar(text('SELECT bool_and(invite_epoch=1) FROM knowledge_bases'))

        def differences(sync: Connection) -> list[Any]:
            return compare_metadata(active_migration_context(sync), schema_metadata())

        assert await connection.run_sync(differences) == []
