"""Upgrade a populated 0001 database without replacing credentials or run identity."""

from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.infrastructure.database import SCHEMA_REVISION


@pytest.mark.asyncio
async def test_upgrade_from_0001_preserves_identity_connection_and_pending_run(settings: object) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    owner_id, connection_id, model_id, run_id = uuid4(), uuid4(), uuid4(), uuid4()
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def initial(connection: object) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, '0001_core')

    try:
        async with engine.begin() as connection:
            await connection.run_sync(initial)
            await connection.execute(
                text(
                    "INSERT INTO identity_users(id,username,password_hash,is_admin,is_active) VALUES (:id,'existing-admin','existing-hash',true,true)"
                ),
                {'id': owner_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO model_connections(id,name,base_url,encrypted_api_key) VALUES (:id,'existing','https://original.invalid/v1','original-ciphertext')"
                ),
                {'id': connection_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO models(id,connection_id,name,upstream_model,max_output_tokens,enabled) VALUES (:id,:connection,'existing','existing-model',64,true)"
                ),
                {'id': model_id, 'connection': connection_id},
            )
            import json

            payload = {
                'binding': {
                    'model_id': str(model_id),
                    'connection_id': str(connection_id),
                    'upstream_model': 'existing-model',
                    'max_output_tokens': 64,
                },
                'messages': [{'role': 'user', 'content': 'original prompt'}],
            }
            await connection.execute(
                text(
                    "INSERT INTO runtime_runs(id,owner_id,resource_type,resource_id,client_request_id,fingerprint,payload,status,attempt_no,revision,projection_seq,provider_started,text,usage_status,created_at) VALUES (:id,:owner,'connection_test',:resource,:request,'original-fingerprint',CAST(:payload AS jsonb),'pending',0,0,0,false,'','pending',now())"
                ),
                {
                    'id': run_id,
                    'owner': owner_id,
                    'resource': model_id,
                    'request': uuid4(),
                    'payload': json.dumps(payload),
                },
            )
        await migrate(settings)
        async with engine.connect() as connection:
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
            assert (
                await connection.scalar(text('SELECT password_hash FROM identity_users WHERE id=:id'), {'id': owner_id})
                == 'existing-hash'
            )
            assert (
                await connection.scalar(
                    text('SELECT encrypted_api_key FROM model_connection_revisions WHERE connection_id=:id'),
                    {'id': connection_id},
                )
                == 'original-ciphertext'
            )
            payload = await connection.scalar(text('SELECT payload FROM runtime_runs WHERE id=:id'), {'id': run_id})
            assert payload['messages'][0]['content'] == 'original prompt'
            assert payload['binding']['connection_revision_id'] == str(connection_id)
            assert (
                await connection.scalar(text('SELECT model_type FROM models WHERE id=:id'), {'id': model_id}) == 'chat'
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_metadata_matches_fresh_migration(app: object) -> None:
    from importlib.util import find_spec

    assert find_spec('zhiheng.bootstrap.schema') is not None
    from alembic.autogenerate import compare_metadata

    from tests.core.schema_support import active_migration_context
    from zhiheng.bootstrap.schema import schema_metadata

    metadata = schema_metadata()
    assert {
        'developer_applications',
        'developer_keys',
        'developer_minute_buckets',
        'model_rate_cards',
        'model_connection_revisions',
    } <= set(metadata.tables)
    async with app.state.container.database.engine.connect() as connection:
        difference = await connection.run_sync(lambda sync: compare_metadata(active_migration_context(sync), metadata))
    assert difference == []
