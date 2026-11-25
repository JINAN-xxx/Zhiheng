from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import Connection, text

from tests.core.integration.test_assistant_langchain import assistant, conversation
from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.database import SCHEMA_REVISION, Database
from zhiheng.shared.errors import AppError


async def test_previous_release_upgrades_existing_private_assistants(settings: Settings) -> None:
    database = Database(settings.database_url.get_secret_value())
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def upgrade_previous(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, '0045_developer_key_controls')

    owner, agent, conversation_id = uuid4(), uuid4(), uuid4()
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(upgrade_previous)
            await connection.execute(
                text(
                    'INSERT INTO identity_users (id, username, password_hash, is_admin, is_active) '
                    "VALUES (:owner, 'migration-test', 'fixture', false, true)"
                ),
                {'owner': owner},
            )
            await connection.execute(
                text(
                    'INSERT INTO agents (id, owner_id, config, version, enabled, created_at, updated_at) '
                    'VALUES (:agent, :owner, \'{"name":"Existing assistant"}\', 4, true, now(), now())'
                ),
                {'agent': agent, 'owner': owner},
            )
            await connection.execute(
                text(
                    'INSERT INTO assistant_conversations (id, owner_id, agent_id, create_request_id, '
                    'config, skills, title, created_at, updated_at) VALUES '
                    '(:conversation, :owner, :agent, :request, \'{"name":"Existing assistant"}\', '
                    "'{\"revisions\":[]}', 'Existing conversation', now(), now())"
                ),
                {'conversation': conversation_id, 'owner': owner, 'agent': agent, 'request': uuid4()},
            )

        with pytest.raises(AppError) as incompatible:
            await database.require_schema()
        assert incompatible.value.code == 'schema_incompatible'

        await migrate(settings)
        await database.require_schema()
        async with database.engine.connect() as connection:
            assistant_row = (
                (
                    await connection.execute(
                        text(
                            'SELECT owner_id, config, version, enabled, plaza_config, plaza_version FROM agents WHERE id = :id'
                        ),
                        {'id': agent},
                    )
                )
                .mappings()
                .one()
            )
            assert assistant_row == {
                'owner_id': owner,
                'config': {'name': 'Existing assistant'},
                'version': 4,
                'enabled': True,
                'plaza_config': None,
                'plaza_version': None,
            }
            chat = (
                (
                    await connection.execute(
                        text('SELECT owner_id, agent_id, title, plaza FROM assistant_conversations WHERE id = :id'),
                        {'id': conversation_id},
                    )
                )
                .mappings()
                .one()
            )
            assert chat == {'owner_id': owner, 'agent_id': agent, 'title': 'Existing conversation', 'plaza': False}
    finally:
        await database.close()


@pytest.mark.parametrize('state', ['personal', 'published', 'archived_chat'])
async def test_plaza_downgrade_preserves_conversation_semantics(
    app: FastAPI, admin: httpx.AsyncClient, model_id: str, state: str
) -> None:
    agent = await assistant(admin, model_id)
    chat = await conversation(admin, agent['id'])
    engine = app.state.container.database.engine
    async with engine.begin() as connection:
        if state == 'published':
            await connection.execute(text('UPDATE agents SET plaza_config = config, plaza_version = version'))
        elif state == 'archived_chat':
            await connection.execute(text('UPDATE assistant_conversations SET plaza = true, deleted_at = now()'))

    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0045_developer_key_controls')

    if state == 'personal':
        async with engine.begin() as connection:
            await connection.run_sync(downgrade)
            assert (
                await connection.scalar(text('SELECT version_num FROM alembic_version'))
                == '0045_developer_key_controls'
            )
            identifiers = (await connection.scalars(text('SELECT id::text FROM assistant_conversations'))).all()
            assert identifiers == [chat['id']]
    else:
        with pytest.raises(RuntimeError, match='Assistant plaza data must be preserved'):
            async with engine.begin() as connection:
                await connection.run_sync(downgrade)
        async with engine.connect() as connection:
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
            assert (await connection.scalar(text('SELECT plaza FROM assistant_conversations'))) is (
                state == 'archived_chat'
            )
            assert (await connection.scalar(text('SELECT plaza_config IS NOT NULL FROM agents'))) is (
                state == 'published'
            )
