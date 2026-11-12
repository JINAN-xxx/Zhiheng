"""Retiring rerank disabling preserves indexed documents, grants and existing model choices."""

from typing import Any
from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from tests.core.integration.test_knowledge import configured_ranker
from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.integration.test_knowledge_materials_v2 import library
from tests.core.provider import ProviderServer
from tests.core.schema_support import active_migration_context, assert_original_rows
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.modules.knowledge.orm import KnowledgeGrant


async def test_rerank_policy_upgrade_preserves_materials_and_existing_valid_choices(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    indexed = await ready_base(admin, app, provider)
    explicit = await library(admin, provider, 'Keep explicit reranker')
    inherited = await library(admin, provider, 'Keep inherited reranker')
    ranker = await configured_ranker(admin, provider)
    container = app.state.container
    async with container.database.sessions.begin() as session:
        user = await session.scalar(text("SELECT id FROM identity_users WHERE username='member'"))
        session.add(KnowledgeGrant(base_id=UUID(indexed['id']), user_id=user, effect='allow'))
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def previous(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0023_knowledge_link_only')

    async with container.database.engine.begin() as connection:
        await connection.run_sync(previous)
        await connection.execute(
            text('UPDATE knowledge_bases SET use_default_rerank=false,rerank_model_id=NULL WHERE id=:id'),
            {'id': UUID(indexed['id'])},
        )
        await connection.execute(
            text('UPDATE knowledge_bases SET use_default_rerank=false,rerank_model_id=:ranker WHERE id=:id'),
            {'id': UUID(explicit['id']), 'ranker': UUID(ranker)},
        )
        before_bases = {
            str(row.id): row._asdict()
            for row in await connection.execute(
                text(
                    'SELECT id,version,use_default_rerank,rerank_model_id,updated_at,active_revision FROM knowledge_bases'
                )
            )
        }
        tables = list(
            await connection.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename!='alembic_version' ORDER BY tablename"
                )
            )
        )
        before = {}
        for table in tables:
            projection = (
                "to_jsonb(t)-'version'-'use_default_rerank'-'updated_at'"
                if table == 'knowledge_bases'
                else 'to_jsonb(t)'
            )
            before[table] = list(
                await connection.scalars(
                    text('SELECT (' + projection + ')::text FROM "' + table + '" t ORDER BY (' + projection + ')::text')
                )
            )
    await migrate(container.settings)
    async with container.database.engine.connect() as connection:
        for table in tables:
            projection = (
                "to_jsonb(t)-'version'-'use_default_rerank'-'updated_at'"
                if table == 'knowledge_bases'
                else 'to_jsonb(t)'
            )
            after = list(
                await connection.scalars(
                    text('SELECT (' + projection + ')::text FROM "' + table + '" t ORDER BY (' + projection + ')::text')
                )
            )
            assert_original_rows(before[table], after, table)
        for row in await connection.execute(
            text('SELECT id,version,use_default_rerank,rerank_model_id,updated_at,active_revision FROM knowledge_bases')
        ):
            old = before_bases[str(row.id)]
            changed = not old['use_default_rerank'] and old['rerank_model_id'] is None
            assert row.version == old['version'] + changed
            assert row.use_default_rerank == (True if changed else old['use_default_rerank'])
            assert row.rerank_model_id == old['rerank_model_id'] and row.active_revision == old['active_revision']
            assert row.updated_at >= old['updated_at'] if changed else row.updated_at == old['updated_at']

        def differences(sync: Connection) -> list[Any]:
            return compare_metadata(active_migration_context(sync), schema_metadata())

        assert await connection.run_sync(differences) == []
    assert (await admin.get('/api/knowledge/bases/' + indexed['id'])).json()['index_status'] == 'ready'
    assert not (await admin.get('/api/knowledge/bases/' + explicit['id'])).json()['use_default_rerank']
    assert (await admin.get('/api/knowledge/bases/' + inherited['id'])).json()['use_default_rerank']
    with pytest.raises(IntegrityError):
        async with container.database.sessions.begin() as session:
            await session.execute(
                text('UPDATE knowledge_bases SET use_default_rerank=false,rerank_model_id=NULL WHERE id=:id'),
                {'id': UUID(indexed['id'])},
            )
