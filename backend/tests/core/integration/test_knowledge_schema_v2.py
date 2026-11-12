"""A populated 0019 upgrade must preserve original data and conservative ACLs."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.core.integration.test_knowledge import VectorFixture
from tests.core.integration.test_knowledge_failures import finish_job
from tests.core.integration.test_knowledge_materials_v2 import library
from tests.core.legacy_schema import reconstruct
from tests.core.provider import ProviderServer
from tests.core.schema_support import active_migration_context, assert_private_base_backfill
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.infrastructure.database import SCHEMA_REVISION
from zhiheng.modules.knowledge.orm import KnowledgeBase
from zhiheng.shared.time import utcnow


async def test_populated_schema19_upgrade_keeps_original_knowledge_and_acl(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    container = app.state.container
    container.knowledge_jobs.vectors = VectorFixture()
    base = await library(admin, provider)
    async with container.database.sessions.begin() as session:
        row = await session.get(KnowledgeBase, UUID(base['id']))
        assert row is not None
        row.document_policy = 'legacy'
    upload = await admin.post(
        '/api/files?filename=legacy-schema19.txt',
        content='原知识库资料和权限保持不变。编号 20261106。'.encode(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert upload.status_code == 201, upload.text
    original_id = upload.json()['id']
    queued = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={'file_ids': [original_id], 'client_request_id': str(uuid4())},
    )
    assert queued.status_code == 202, queued.text
    assert (await finish_job(admin, app, queued.json()))['status'] == 'completed'
    documents = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items']
    assert len(documents) == 1 and documents[0]['file_id'] == original_id
    async with container.database.sessions.begin() as session:
        member_id = await session.scalar(text("SELECT id FROM identity_users WHERE username='member'"))
        await session.execute(
            text(
                "INSERT INTO knowledge_grants (base_id,user_id,effect,expires_at) VALUES (:base,:user,'deny',:expires)"
            ),
            {'base': UUID(base['id']), 'user': member_id, 'expires': utcnow() + timedelta(days=2)},
        )
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def old_schema(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, '0019_developer_key_deletion')

    async with container.database.engine.begin() as connection:
        await reconstruct(connection, old_schema, preserve_personal_skills=True)
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0019_developer_key_deletion'
        await connection.execute(
            text("UPDATE knowledge_bases SET access='public' WHERE id=:id"), {'id': UUID(base['id'])}
        )
        columns = (
            await connection.execute(
                text(
                    "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' AND table_name!='alembic_version' ORDER BY table_name,ordinal_position"
                )
            )
        ).all()
        tables = sorted({column[0] for column in columns})
        snapshots = {
            table: (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1')))
            .scalars()
            .all()
            for table in tables
        }
        assert len(snapshots['knowledge_documents']) == len(snapshots['knowledge_generations']) == 1
        assert len(snapshots['knowledge_grants']) == 1

    await migrate(container.settings)
    async with container.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
        for table in tables:
            names = [column[1] for column in columns if column[0] == table]
            current = (
                (
                    await connection.execute(
                        text(f'SELECT to_jsonb(t)::text FROM (SELECT {",".join(names)} FROM {table}) t ORDER BY 1')
                    )
                )
                .scalars()
                .all()
            )
            if table == 'knowledge_bases':
                assert_private_base_backfill(snapshots[table], current)
            else:
                assert current == snapshots[table], table
        assert (await connection.execute(text('SELECT document_policy FROM knowledge_bases'))).scalars().all() == [
            'legacy'
        ]
        assert (
            await connection.execute(text('SELECT retrieval_version FROM knowledge_generations'))
        ).scalars().all() == [1]
        assert str(await connection.scalar(text('SELECT file_id FROM knowledge_documents'))) == original_id
        assert await connection.scalar(text('SELECT count(*) FROM knowledge_materials')) == 0
        assert await connection.scalar(text('SELECT count(*) FROM knowledge_migrations')) == 0
        defaults = (
            await connection.execute(
                text(
                    'SELECT id,default_embed_model_id,default_rerank_model_id,chunk_size,chunk_overlap,version FROM knowledge_defaults'
                )
            )
        ).one()
        assert tuple(defaults) == (1, None, None, 1000, 200, 1)

        def compare(sync: Connection) -> list[Any]:
            return compare_metadata(active_migration_context(sync), schema_metadata())

        assert await connection.run_sync(compare) == []
