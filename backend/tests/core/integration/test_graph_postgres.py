import secrets
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from tests.core.resources import resource
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.knowledge.adapters.lightrag.postgres import PostgresContext
from zhiheng.shared.errors import AppError


async def test_real_explicit_pool_reconnect_rejects_catalog_and_owner(  # noqa: PLR0915 - verify catalog faults and restored storage against the same owned pool and role
    settings: Settings,
) -> None:
    await migrate(settings)
    url = make_url(settings.database_url.get_secret_value().replace('postgresql+asyncpg:', 'postgresql:'))
    admin = await asyncpg.connect(url.render_as_string(hide_password=False))
    role = 'graph_run_' + uuid4().hex
    password = secrets.token_urlsafe(24)
    resource('intent', 'postgres_role', role, database=url.database)
    try:
        await admin.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\'')
        resource('created', 'postgres_role', role, database=url.database)
        await admin.execute(f'REVOKE CREATE,TEMPORARY ON DATABASE "{url.database}" FROM PUBLIC')
        await admin.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        await admin.execute(f'GRANT USAGE ON SCHEMA knowledge_graph_sdk TO "{role}"')
        await admin.execute(
            f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA knowledge_graph_sdk TO "{role}"'
        )
        runtime = url.set(username=role, password=password).render_as_string(hide_password=False)
        context = PostgresContext(url=runtime, workspace='g_' + uuid4().hex, read_only=False, check=lambda: None)
        await context.open()
        await context.reconnect()
        original_owner = await admin.fetchval('SELECT current_user')
        member_role = 'graph_member_' + uuid4().hex
        resource('intent', 'postgres_role', member_role, database=url.database)
        await admin.execute(f'CREATE ROLE "{member_role}" NOLOGIN')
        resource('created', 'postgres_role', member_role, database=url.database)
        try:
            await admin.execute(f'ALTER TABLE knowledge_graph_sdk.lightrag_doc_full OWNER TO "{role}"')
            with pytest.raises(AppError):
                await context.verify()
            await admin.execute(
                'ALTER TABLE knowledge_graph_sdk.lightrag_doc_full OWNER TO "' + original_owner.replace('"', '""') + '"'
            )
            await admin.execute(
                f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA knowledge_graph_sdk TO "{role}"'
            )
            await admin.execute(f'GRANT "{member_role}" TO "{role}"')
            with pytest.raises(AppError):
                await context.verify()
            await admin.execute(f'REVOKE "{member_role}" FROM "{role}"')
            await admin.execute(f'ALTER ROLE "{role}" BYPASSRLS')
            with pytest.raises(AppError):
                await context.verify()
            await admin.execute(f'ALTER ROLE "{role}" NOBYPASSRLS')
            await admin.execute(f'REVOKE UPDATE ON knowledge_graph_sdk.lightrag_doc_chunks FROM "{role}"')
            with pytest.raises(AppError):
                await context.verify()
            await admin.execute(f'GRANT UPDATE ON knowledge_graph_sdk.lightrag_doc_chunks TO "{role}"')
            await context.verify()
        finally:
            await admin.execute(f'REVOKE "{member_role}" FROM "{role}"')
            await admin.execute(f'DROP ROLE "{member_role}"')
            resource('deleted', 'postgres_role', member_role, database=url.database)
        try:
            await admin.execute('DROP INDEX knowledge_graph_sdk.idx_lightrag_doc_full_id')
            with pytest.raises(AppError, match='图谱数据库'):
                await context.verify()
            await admin.execute('CREATE INDEX idx_lightrag_doc_full_id ON knowledge_graph_sdk.lightrag_doc_full(id)')
            await context.verify()
            await admin.execute('ALTER TABLE knowledge_graph_sdk.lightrag_doc_full ADD COLUMN unexpected INTEGER')
            with pytest.raises(AppError):
                await context.verify()
            await admin.execute('ALTER TABLE knowledge_graph_sdk.lightrag_doc_full DROP COLUMN unexpected')
            await admin.execute('ALTER TABLE knowledge_graph_sdk.lightrag_doc_full RENAME TO misplaced_doc_full')
            with pytest.raises(AppError):
                await context.reconnect()
            await admin.execute('ALTER TABLE knowledge_graph_sdk.misplaced_doc_full RENAME TO lightrag_doc_full')
            await context.reconnect()
            from lightrag.base import DocStatus
            from lightrag.kg.shared_storage import finalize_share_data, initialize_share_data

            from zhiheng.modules.knowledge.adapters.lightrag.postgres import CURRENT_STORAGE
            from zhiheng.modules.knowledge.adapters.lightrag.storage import GraphDocStorage, GraphKVStorage
            from zhiheng.shared.time import utcnow

            initialize_share_data()
            token = CURRENT_STORAGE.set(context)
            status = GraphDocStorage('doc_status', context.workspace, {}, None)
            full = GraphKVStorage('full_docs', context.workspace, {}, None)
            try:
                await status.initialize()
                await full.initialize()
                now = utcnow().isoformat()
                rows = {
                    key: {
                        'status': 'pending',
                        'content_summary': key,
                        'content_length': 4,
                        'file_path': 'managed/' + key,
                        'created_at': now,
                        'updated_at': now,
                        'track_id': 'owned-track',
                        'metadata': {},
                        'content_hash': 'c' * 64,
                    }
                    for key in ('doc-one', 'doc-two')
                }
                await full.upsert({key: {'content': key, 'file_path': 'managed/' + key} for key in rows})
                await status.upsert(rows)
                assert (await status.get_by_id_strict('doc-one'))['status'] == 'pending'
                assert len(await status.get_by_ids(list(rows))) == 2
                assert await status.filter_keys({'doc-one', 'new'}) == {'new'}
                assert (await status.get_status_counts())['pending'] == 2
                assert len(await status.get_docs_by_statuses([DocStatus.PENDING])) == 2
                paginated, total = await status.get_docs_paginated(page=1, page_size=10)
                assert len(paginated) == 2 and total == 2
                await status.update_doc_status_fields(
                    'doc-one', {'status': 'processed', 'metadata': {'verified': True}}
                )
                assert (await status.get_by_id_strict('doc-one'))['status'] == 'processed'
                assert await status.count_docs_by_statuses([DocStatus.PROCESSED]) == 1
                context.read_only = True
                with pytest.raises(AppError, match='禁止写入'):
                    await status.upsert({'denied': rows['doc-one']})
                context.read_only = False
                await status.delete(list(rows))
                await full.delete(list(rows))
                assert await status.is_empty() and await full.is_empty()
                async with context.db.pool.acquire() as connection:
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await connection.execute('CREATE TABLE knowledge_graph_sdk.forbidden(id int)')
            finally:
                await status.finalize()
                await full.finalize()
                CURRENT_STORAGE.reset(token)
                finalize_share_data()
        finally:
            await context.close()
        owner = PostgresContext(
            url=url.render_as_string(hide_password=False), workspace='owner', read_only=False, check=lambda: None
        )
        with pytest.raises(AppError, match='图谱数据库'):
            await owner.open()
    finally:
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        resource('deleted', 'postgres_role', role, database=url.database)
        await admin.close()
