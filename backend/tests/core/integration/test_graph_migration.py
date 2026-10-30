from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.core.integration.test_knowledge_milvus import real_vectors  # noqa: F401
from zhiheng.bootstrap.migrations import migrate
from zhiheng.infrastructure.database import SCHEMA_REVISION
from zhiheng.modules.knowledge.adapters.lightrag.schema import GRAPH_SCHEMA


async def _finish_query(container: Any, admin: Any, info: dict[str, Any]) -> dict[str, Any]:
    from uuid import UUID

    from sqlalchemy import select

    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    for _ in range(15):
        await container.knowledge_preparation.tick(UUID(info['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(container, run_id)
        info = (await admin.get('/api/knowledge/preparations/' + info['id'])).json()
        if info['status'] == 'completed':
            return info
    raise AssertionError(info)


async def test_graph_schema_has_complete_sdk_catalog_and_business_inventory(settings: object) -> None:
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
            assert (
                await connection.scalar(
                    text('SELECT count(*) FROM pg_tables WHERE schemaname=:schema'), {'schema': GRAPH_SCHEMA}
                )
                == 10
            )
            constraints = dict(
                (
                    await connection.execute(
                        text(
                            'SELECT contype::text,count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace WHERE n.nspname=:schema GROUP BY contype'
                        ),
                        {'schema': GRAPH_SCHEMA},
                    )
                ).all()
            )
            assert constraints == {'p': 10, 'f': 2}
            assert (
                await connection.scalar(
                    text('SELECT count(*) FROM pg_indexes WHERE schemaname=:schema'), {'schema': GRAPH_SCHEMA}
                )
                == 36
            )
    finally:
        await engine.dispose()


@pytest.mark.usefixtures('real_vectors')
async def test_populated_schema7_real_vector_replay_and_fingerprint_survive_graph_upgrade(
    admin: object,
    app: object,
    provider: object,
) -> None:
    from uuid import UUID, uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import select

    from tests.core.integration.test_knowledge_roles import indexed_reference
    from zhiheng.bootstrap.migrations import migration_directory
    from zhiheng.modules.knowledge.orm import IndexGeneration

    container = app.state.container
    _, base = await indexed_reference(admin, app, provider)
    async with container.database.sessions() as session:
        generation = await session.scalar(
            select(IndexGeneration).where(
                IndexGeneration.base_id == UUID(base['id']), IndexGeneration.status == 'active'
            )
        )
        revision = generation.id
        fingerprint = generation.fingerprint
        collection = generation.collection_name
        assert (
            'response_format' not in generation.binding['options']
            and 'reasoning_effort' not in generation.binding['options']
        )

    body = {'query': 'reference', 'client_request_id': str(uuid4()), 'top_k': 1}
    endpoint = f'/api/knowledge/bases/{base["id"]}/query'
    first = await admin.post(endpoint, json=body)
    assert first.status_code == 202, first.text
    prepared = await _finish_query(container, admin, first.json())
    assert prepared['hits']
    before_http = len(provider.requests)
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: object) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0007_knowledge')

    async with container.database.engine.begin() as connection:
        await connection.run_sync(downgrade)
        snapshots = {
            table: (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1')))
            .scalars()
            .all()
            for table in (
                'knowledge_generations',
                'knowledge_documents',
                'knowledge_chunks',
                'runtime_runs',
                'developer_invocations',
            )
        }
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0007_knowledge'
    await migrate(container.settings)
    async with container.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
        for table, rows in snapshots.items():
            assert (
                await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1'))
            ).scalars().all() == rows
    replay = await admin.post(endpoint, json=body)
    assert replay.status_code == 202 and replay.json()['id'] == prepared['id'], replay.text
    assert len(provider.requests) == before_http
    fresh = await admin.post(endpoint, json={**body, 'client_request_id': str(uuid4())})
    assert fresh.status_code == 202, fresh.text
    current = await _finish_query(container, admin, fresh.json())
    assert current['hits'] and len(provider.requests) == before_http + 1
    info = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
    assert info['active_revision'] == str(revision) and info['index_status'] == 'ready'
    async with container.database.sessions() as session:
        retained = await session.get(IndexGeneration, revision)
        assert (
            retained.collection_name == collection
            and retained.fingerprint == fingerprint
            and retained.status == 'active'
        )
