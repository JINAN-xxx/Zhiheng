from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.bootstrap.settings import Settings


async def _version(settings: Settings, target: str, *, downgrade: bool = False) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    config = Config()
    config.attributes['target_metadata'] = schema_metadata()
    config.set_main_option('script_location', str(migration_directory()))

    def run(connection: Connection) -> None:
        config.attributes['connection'] = connection
        (command.downgrade if downgrade else command.upgrade)(config, target)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(run)
    finally:
        await engine.dispose()


async def test_upgrade_links_existing_admin_and_preserves_same_code_custom_role(settings: Settings) -> None:
    await _version(settings, '0010_admin_console')
    engine = create_async_engine(settings.database_url.get_secret_value())
    admin_id, ordinary_id = uuid4(), uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    'INSERT INTO identity_users (id,username,password_hash,is_admin,is_active) '
                    "VALUES (:id,'historical-admin','historical-password-hash',true,true)"
                ),
                {'id': admin_id},
            )
            await connection.execute(
                text(
                    'INSERT INTO identity_roles (id,code,name,permissions,is_active,remark,version,created_at,updated_at) '
                    "VALUES (:id,'super_admin','已有业务角色','[\"system:users:read\"]'::json,true,'原有备注',1,now(),now())"
                ),
                {'id': ordinary_id},
            )
            await connection.execute(
                text('INSERT INTO identity_user_roles (user_id,role_id) VALUES (:user_id,:role_id)'),
                {'user_id': admin_id, 'role_id': ordinary_id},
            )
        await _version(settings, '0011_builtin_admin_role')
        async with engine.connect() as connection:
            system = (
                await connection.execute(
                    text("SELECT id,code,name,is_active,permissions FROM identity_roles WHERE system_key='super_admin'")
                )
            ).one()
            assert system.name == '超级管理员' and system.is_active
            assert system.code != 'super_admin' and system.permissions == []
            ordinary = (
                await connection.execute(
                    text('SELECT name,permissions,remark FROM identity_roles WHERE id=:id'), {'id': ordinary_id}
                )
            ).one()
            assert tuple(ordinary) == ('已有业务角色', ['system:users:read'], '原有备注')
            role_ids = set(
                await connection.scalars(
                    text('SELECT role_id FROM identity_user_roles WHERE user_id=:id'), {'id': admin_id}
                )
            )
            assert role_ids == {ordinary_id, system.id}
            assert (
                await connection.scalar(text('SELECT password_hash FROM identity_users WHERE id=:id'), {'id': admin_id})
                == 'historical-password-hash'
            )
        await _version(settings, '0011_builtin_admin_role')
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT count(*) FROM identity_roles WHERE system_key='super_admin'")) == 1
            )
        await _version(settings, '0010_admin_console', downgrade=True)
        async with engine.connect() as connection:
            assert list(await connection.scalars(text('SELECT id FROM identity_roles'))) == [ordinary_id]
            assert list(
                await connection.scalars(
                    text('SELECT role_id FROM identity_user_roles WHERE user_id=:id'), {'id': admin_id}
                )
            ) == [ordinary_id]
        await migrate(settings)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text('SELECT password_hash FROM identity_users WHERE id=:id'), {'id': admin_id})
                == 'historical-password-hash'
            )
            assert (
                await connection.scalar(text("SELECT count(*) FROM identity_roles WHERE system_key='super_admin'")) == 1
            )
    finally:
        await engine.dispose()


async def test_fresh_install_has_one_builtin_role(settings: Settings) -> None:
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM identity_roles WHERE system_key='super_admin' AND name='超级管理员'")
                )
                == 1
            )
            assert await connection.scalar(text('SELECT count(*) FROM identity_users')) == 0
    finally:
        await engine.dispose()
