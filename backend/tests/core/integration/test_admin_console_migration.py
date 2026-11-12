from datetime import datetime, timedelta, timezone
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.bootstrap.settings import Settings


async def _version(engine: AsyncEngine, target: str, *, downgrade: bool = False) -> None:
    configuration = Config()
    configuration.set_main_option('script_location', str(migration_directory()))
    configuration.attributes['target_metadata'] = schema_metadata()

    def run(connection: Connection) -> None:
        configuration.attributes['connection'] = connection
        (command.downgrade if downgrade else command.upgrade)(configuration, target)

    async with engine.begin() as connection:
        await connection.run_sync(run)


async def _original_rows(engine: AsyncEngine) -> tuple[list[tuple], list[tuple]]:
    async with engine.connect() as connection:
        users = await connection.execute(
            text('SELECT id,username,password_hash,is_admin,is_active FROM identity_users ORDER BY id')
        )
        sessions = await connection.execute(
            text('SELECT token_hash,user_id,expires_at FROM identity_sessions ORDER BY token_hash')
        )
        return [tuple(row) for row in users], [tuple(row) for row in sessions]


async def test_admin_upgrade_and_downgrade_keep_old_accounts_and_sessions(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        await _version(engine, '0009_message_actions')
        identifier = uuid4()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    'INSERT INTO identity_users (id,username,password_hash,is_admin,is_active) '
                    'VALUES (:id,:username,:hash,true,true)'
                ),
                {'id': identifier, 'username': 'historical-admin', 'hash': 'existing-hash-preserved'},
            )
            for token in ['a' * 64, 'b' * 64]:
                await connection.execute(
                    text('INSERT INTO identity_sessions (token_hash,user_id,expires_at) VALUES (:token,:id,:expires)'),
                    {'token': token, 'id': identifier, 'expires': datetime.now(timezone.utc) + timedelta(days=1)},
                )
        original = await _original_rows(engine)
        await _version(engine, '0010_admin_console')
        assert await _original_rows(engine) == original
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text('SELECT count(*),count(DISTINCT id),bool_and(created_at IS NULL) FROM identity_sessions')
                )
            ).one()
            assert tuple(row) == (2, 2, True)
            user = (await connection.execute(text('SELECT version,created_at,updated_at FROM identity_users'))).one()
            assert tuple(user) == (1, None, None)
        await _version(engine, '0009_message_actions', downgrade=True)
        assert await _original_rows(engine) == original
        await migrate(settings)
        assert await _original_rows(engine) == original
    finally:
        await engine.dispose()
