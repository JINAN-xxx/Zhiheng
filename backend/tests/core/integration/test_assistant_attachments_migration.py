"""Schema41 preserves original assistant history and refuses lossy downgrade."""

from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.core.integration.test_assistant_attachments import send, upload
from tests.core.integration.test_assistant_conversations import assistant, conversation, submit
from tests.core.legacy_schema import reconstruct
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.infrastructure.database import SCHEMA_REVISION
from zhiheng.worker.execution import execute_run


async def test_populated40_upgrade_preserves_original_rows_and_refuses_attachment_loss(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    c = app.state.container
    agent = await assistant(admin, model_id)
    session = await conversation(admin, agent['id'])
    run = await submit(admin, agent['id'], session['id'], 'Old text message')
    await execute_run(c, UUID(run['id']))
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def original_schema(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, '0040_assistant_agents')

    async with c.database.engine.begin() as connection:
        assert (
            await connection.scalar(text('SELECT status FROM runtime_runs WHERE id=:id'), {'id': UUID(run['id'])})
            == 'completed'
        )
        old = list(
            (
                await connection.execute(
                    text("SELECT (to_jsonb(t)-'attachments')::text FROM assistant_turns t ORDER BY id")
                )
            ).scalars()
        )
        finances = {
            table: list(
                (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1'))).scalars()
            )
            for table in ['billing_accounts', 'billing_control', 'billing_ledger', 'developer_invocations']
        }
        await reconstruct(connection, original_schema, preserve_personal_skills=True, preserve_checkpoints=True)
    await c.database.engine.dispose()
    await migrate(c.settings)
    async with c.database.engine.connect() as connection:
        assert (
            list(
                (
                    await connection.execute(
                        text("SELECT (to_jsonb(t)-'attachments')::text FROM assistant_turns t ORDER BY id")
                    )
                ).scalars()
            )
            == old
        )
        assert list((await connection.execute(text('SELECT attachments FROM assistant_turns'))).scalars()) == [[]]
        for table, rows in finances.items():
            assert (
                list((await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1'))).scalars())
                == rows
            )
    file = await upload(admin, agent['id'])
    accepted = await send(admin, agent['id'], session['id'], [file['id']])
    assert accepted.status_code == 202, accepted.text
    await execute_run(c, UUID(accepted.json()['id']))
    async with c.database.engine.connect() as connection:
        assert (
            await connection.scalar(
                text('SELECT status FROM runtime_runs WHERE id=:id'), {'id': UUID(accepted.json()['id'])}
            )
            == 'completed'
        )
        assert (
            await connection.scalar(text("SELECT count(*) FROM assistant_turns WHERE attachments != '[]'::jsonb")) == 1
        )

    def downgrade(connection: Connection) -> None:
        config.attributes['connection'] = connection
        # A downgrade to the shared ancestor must retain both branches when
        # attachment history makes the operation unsafe.
        command.downgrade(config, '0040_assistant_agents')

    with pytest.raises(RuntimeError, match='attachment history must be preserved'):
        async with c.database.engine.begin() as connection:
            await connection.run_sync(downgrade)
    async with c.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
        assert await connection.scalar(text("SELECT to_regclass('work_checkpoints.checkpoints') IS NOT NULL"))
