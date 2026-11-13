"""An existing installation keeps populated/shared libraries and all source identities."""

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
from tests.core.integration.test_knowledge_materials_v2 import library
from tests.core.provider import ProviderServer
from tests.core.schema_support import active_migration_context
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.modules.knowledge.orm import KnowledgeBase


async def test_settings_upgrade_preserves_populated_and_shared_legacy_libraries(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    populated = await ready_base(admin, app, provider)
    empty = await library(admin, provider, 'Empty private')
    shared = await library(admin, provider, 'Empty shared')
    container = app.state.container
    async with container.database.sessions.begin() as session:
        for base in (populated, empty, shared):
            row = await session.get(KnowledgeBase, UUID(base['id']))
            row.document_policy = 'legacy'
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def previous(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0020_knowledge_library')

    async with container.database.engine.begin() as connection:
        await connection.run_sync(previous)
        await connection.execute(
            text("UPDATE knowledge_bases SET access='public' WHERE id=:id"), {'id': UUID(shared['id'])}
        )
        old = (
            await connection.execute(
                text('SELECT id,version,active_revision,embed_model_id FROM knowledge_bases ORDER BY id')
            )
        ).all()
        files = (
            await connection.execute(text('SELECT id,storage_key,sha256,owner_id,status FROM files ORDER BY id'))
        ).all()
        generations = (
            await connection.execute(
                text('SELECT id,binding,fingerprint,dimension FROM knowledge_generations ORDER BY id')
            )
        ).all()
    await migrate(container.settings)
    async with container.database.engine.connect() as connection:
        assert (
            await connection.execute(text('SELECT id,storage_key,sha256,owner_id,status FROM files ORDER BY id'))
        ).all() == files
        assert (
            await connection.execute(
                text('SELECT id,binding,fingerprint,dimension FROM knowledge_generations ORDER BY id')
            )
        ).all() == generations
        current = (
            await connection.execute(
                text('SELECT id,version,active_revision,embed_model_id FROM knowledge_bases ORDER BY id')
            )
        ).all()
        for before, after in zip(old, current, strict=True):
            assert before[0] == after[0] and tuple(before[2:]) == tuple(after[2:])
            assert after[1] == before[1] + (1 if str(before[0]) == empty['id'] else 0)
        policies = dict((await connection.execute(text('SELECT id,document_policy FROM knowledge_bases'))).all())
        assert policies[UUID(empty['id'])] == 'library'
        assert policies[UUID(populated['id'])] == policies[UUID(shared['id'])] == 'legacy'
        assert await connection.scalar(text('SELECT count(*) FROM knowledge_upload_receipts')) == 0
        assert await connection.scalar(text('SELECT bool_and(use_default_rerank) FROM knowledge_bases'))

        def differences(sync: Connection) -> list[Any]:
            return compare_metadata(active_migration_context(sync), schema_metadata())

        assert await connection.run_sync(differences) == []
