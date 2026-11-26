from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.engine import Connection

from tests.core.permission_support import grants
from zhiheng.bootstrap.migrations import migration_directory
from zhiheng.infrastructure.database import SCHEMA_REVISION
from zhiheng.modules.identity.admin_orm import IdentityRole
from zhiheng.shared.permission_policy import effective_permissions


async def test_role_migration_preserves_existing_operations_and_gates(app: FastAPI) -> None:
    database = app.state.container.database
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def downgrade(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.downgrade(config, '0050_mcp_connections')

    def upgrade(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')

    async with database.engine.begin() as connection:
        await connection.run_sync(downgrade)
    legacy = {
        'ops-manager': grants('platform:mcp', 'manage') | grants('platform:agents', 'use') | grants('platform:chat'),
        'ops-disabled': grants('platform:mcp', 'manage') - {'platform:mcp:enabled'},
        'ops-reader': grants('platform:mcp') | grants('platform:agents'),
    }
    async with database.sessions.begin() as session:
        for code, permissions in legacy.items():
            session.add(
                IdentityRole(code=code, name=code, permissions=sorted(permissions), is_active=code != 'ops-disabled')
            )
    async with database.engine.begin() as connection:
        await connection.run_sync(upgrade)
        assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
    async with database.sessions() as session:
        roles = {
            row.code: row for row in await session.scalars(select(IdentityRole).where(IdentityRole.code.in_(legacy)))
        }
        mcp_operations = {'platform:mcp:configure', 'platform:mcp:grant', 'platform:mcp:audit'}
        manager = roles['ops-manager']
        assert set(manager.permissions) == legacy['ops-manager'] - {'platform:mcp:manage'} | mcp_operations | {
            'platform:agents:conversation_update',
            'platform:agents:conversation_delete',
            'platform:chat:feedback',
        }
        assert manager.is_active and manager.version == 2
        inactive = roles['ops-disabled']
        assert set(inactive.permissions) == legacy['ops-disabled'] - {'platform:mcp:manage'} | mcp_operations
        assert not inactive.is_active and inactive.version == 2
        assert not effective_permissions(inactive.permissions)
        reader = roles['ops-reader']
        assert set(reader.permissions) == legacy['ops-reader'] and reader.version == 1
