"""Schema41 preserves original assistant history and refuses lossy downgrade."""

from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text

from tests.core.integration.test_assistant_attachments import send, upload
from tests.core.integration.test_assistant_conversations import assistant, conversation, submit
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.worker.execution import execute_run


async def test_populated40_upgrade_preserves_original_rows_and_refuses_attachment_loss(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    c = app.state.container
    agent = await assistant(admin, model_id)
    session = await conversation(admin, agent['id'])
    run = await submit(admin, agent['id'], session['id'], 'Old text message')
    await execute_run(c, UUID(run['id']))
    async with c.database.engine.begin() as connection:
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
        await connection.execute(text('ALTER TABLE assistant_turns DROP COLUMN attachments'))
        await connection.execute(text("UPDATE alembic_version SET version_num='0040_assistant_agents'"))
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
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: object) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0040_assistant_agents')

    with pytest.raises(RuntimeError, match='attachment history must be preserved'):
        async with c.database.engine.begin() as connection:
            await connection.run_sync(downgrade)
    async with c.database.engine.connect() as connection:
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0041_assistant_attachments'
