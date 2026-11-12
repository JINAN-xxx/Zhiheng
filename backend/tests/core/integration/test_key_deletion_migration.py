"""Adding deletion metadata preserves every existing key and its credential digest."""

from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.database import SCHEMA_REVISION


async def test_key_deletion_upgrade_downgrade_and_reupgrade_preserve_existing_keys(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def move(connection: object, target: str, *, downgrade: bool = False) -> None:
        config.attributes['connection'] = connection
        (command.downgrade if downgrade else command.upgrade)(config, target)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(move, '0018_file_job_cursor')
            owner, app_id = uuid4(), uuid4()
            await connection.execute(
                text(
                    "INSERT INTO identity_users(id,username,password_hash,is_admin,is_active) VALUES (:id,'old-owner','original-hash',false,true)"
                ),
                {'id': owner},
            )
            await connection.execute(
                text(
                    "INSERT INTO developer_applications(id,code,name,owner_id,status,version,created_at,updated_at) VALUES (:id,'existing','Original',:owner,'active',1,now(),now())"
                ),
                {'id': app_id, 'owner': owner},
            )
            for status in ['active', 'retiring', 'revoked']:
                await connection.execute(
                    text(
                        "INSERT INTO developer_keys(id,application_id,name,prefix,digest,status,model_ids,scopes,resource_rpm_limit,currency,created_at,retire_at) VALUES (:id,:app,:name,'original-prefix',:digest,:status,'[]','[\"files:read\"]',120,'USD',now(),CASE WHEN :is_retiring THEN now()+interval '1 hour' ELSE NULL END)"
                    ),
                    {
                        'id': uuid4(),
                        'app': app_id,
                        'name': status,
                        'digest': status + '-original-digest',
                        'status': status,
                        'is_retiring': status == 'retiring',
                    },
                )
            before = list(
                (await connection.execute(text('SELECT to_jsonb(k)::text FROM developer_keys k ORDER BY id'))).scalars()
            )
        await migrate(settings)
        async with engine.begin() as connection:
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
            after = list(
                (
                    await connection.execute(
                        text("SELECT (to_jsonb(k)-'deleted_at')::text FROM developer_keys k ORDER BY id")
                    )
                ).scalars()
            )
            assert after == before
            assert (
                await connection.scalar(text('SELECT count(*) FROM developer_keys WHERE deleted_at IS NOT NULL')) == 0
            )
            # Downgrading must not resurrect an already deleted credential.
            await connection.execute(
                text("UPDATE developer_keys SET status='revoked',retire_at=NULL,deleted_at=now() WHERE name='active'")
            )
            revoked = await connection.scalar(text("SELECT id FROM developer_keys WHERE name='active'"))
            await connection.run_sync(move, '0018_file_job_cursor', downgrade=True)
            assert (
                await connection.scalar(text('SELECT status FROM developer_keys WHERE id=:id'), {'id': revoked})
                == 'revoked'
            )
        await migrate(settings)
        async with engine.connect() as connection:
            assert await connection.scalar(text('SELECT count(*) FROM developer_keys')) == 3
            assert (
                await connection.scalar(text('SELECT status FROM developer_keys WHERE id=:id'), {'id': revoked})
                == 'revoked'
            )
    finally:
        await engine.dispose()
