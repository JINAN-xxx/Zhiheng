import json
from typing import Any
from uuid import UUID, uuid4

import httpx
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.core.legacy_schema import reconstruct
from tests.core.schema_support import active_migration_context
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.infrastructure.database import SCHEMA_REVISION
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


async def test_populated_schema6_and_old_payload_replay_survive_upgrade(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: Any,
) -> None:
    container = app.state.container
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    body = {'message': 'original schema6 request', 'model_id': model_id, 'client_request_id': str(uuid4())}
    endpoint = f'/api/chat/conversations/{conversation["id"]}/runs'
    admitted = (await admin.post(endpoint, json=body)).json()
    async with container.database.sessions.begin() as session:
        run = await session.get(Run, UUID(admitted['id']))
        payload = dict(run.payload)
        payload.pop('execution_timeout_seconds', None)
        payload.pop('execution_deadline', None)
        run.payload = payload
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, '0006_files')

    async with container.database.engine.begin() as connection:
        await reconstruct(connection, downgrade)
        columns = (
            await connection.execute(
                text(
                    "SELECT table_name,column_name,udt_name,is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name!='alembic_version' ORDER BY table_name,ordinal_position"
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
    await migrate(container.settings)
    async with container.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
        for table in tables:
            selector = ''
            if table in {'skills', 'skill_revisions'}:
                identifiers = [json.loads(row)['id'] for row in snapshots[table]]
                selector = (
                    ' WHERE id IN (' + ','.join("'" + value + "'" for value in identifiers) + ')'
                    if identifiers
                    else ' WHERE false'
                )

            names = [column[1] for column in columns if column[0] == table]
            current = (
                (
                    await connection.execute(
                        text(
                            f'SELECT to_jsonb(t)::text FROM (SELECT {",".join(names)} FROM {table}{selector}) t ORDER BY 1'
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert current == snapshots[table]

        def compare(sync: Connection) -> list[Any]:
            return compare_metadata(active_migration_context(sync), schema_metadata())

        assert await connection.run_sync(compare) == []
    await execute_run(container, UUID(admitted['id']))
    replay = await admin.post(endpoint, json=body)
    assert replay.status_code == 202 and replay.json()['id'] == admitted['id']
    assert replay.json()['status'] == 'completed'
    assert len(provider.requests) == 1
    assert len((await admin.get('/api/usage')).json()['records']) == 1
