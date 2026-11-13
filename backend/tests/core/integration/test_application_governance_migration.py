"""Adding the platform switch preserves owner state, keys and credit history."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.core.integration.test_personal_skill_migration import _version
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.database import SCHEMA_REVISION


async def test_governance_upgrade_preserves_records_and_downgrade_cannot_reactivate_suspended_apps(
    settings: Settings,
) -> None:
    await _version(settings, '0028_document_parsing')
    engine = create_async_engine(settings.database_url.get_secret_value())
    owner, active, disabled, key = (uuid4() for _ in range(4))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO identity_users (id,username,password_hash,is_admin,is_active) VALUES (:id,'original-owner','original-hash',false,true)"
                ),
                {'id': owner},
            )
            for identifier, status in [(active, 'active'), (disabled, 'disabled')]:
                await connection.execute(
                    text("""INSERT INTO developer_applications
                    (id,code,name,owner_id,status,version,created_at,updated_at)
                    VALUES (:id,:code,'Existing',:owner,:status,4,now(),now())"""),
                    {'id': identifier, 'code': identifier.hex, 'owner': owner, 'status': status},
                )
            await connection.execute(
                text("""INSERT INTO developer_keys
                (id,application_id,name,prefix,digest,status,model_ids,created_at,currency)
                VALUES (:id,:app,'Existing key','prefix','original-digest','active','[]'::jsonb,now(),'CNY')"""),
                {'id': key, 'app': active},
            )
            await connection.execute(
                text("""INSERT INTO billing_accounts
                (user_id,enabled,allocated_points,reclaimed_points,used_points,reserved_points,version)
                VALUES (:owner,true,5,0,1,0,7)"""),
                {'owner': owner},
            )
            tables = ['identity_users', 'developer_keys', 'billing_accounts']
            before = {
                table: list(
                    (await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1'))).scalars()
                )
                for table in tables
            }
            apps = list(
                (
                    await connection.execute(
                        text(
                            'SELECT id,status,version,source_file_limit,source_byte_limit FROM developer_applications ORDER BY id'
                        )
                    )
                ).all()
            )
        await migrate(settings)
        async with engine.connect() as connection:
            for table in tables:
                assert (
                    list(
                        (
                            await connection.execute(text(f'SELECT to_jsonb(t)::text FROM {table} t ORDER BY 1'))
                        ).scalars()
                    )
                    == before[table]
                )
            assert (
                list(
                    (
                        await connection.execute(
                            text(
                                'SELECT id,status,version,source_file_limit,source_byte_limit FROM developer_applications ORDER BY id'
                            )
                        )
                    ).all()
                )
                == apps
            )
            assert (
                await connection.execute(text('SELECT platform_enabled FROM developer_applications'))
            ).scalars().all() == [True, True]
        await _version(settings, '0028_document_parsing', downgrade=True)
        await migrate(settings)
        async with engine.begin() as connection:
            await connection.execute(
                text('UPDATE developer_applications SET platform_enabled=false WHERE id=:id'), {'id': active}
            )
        with pytest.raises(RuntimeError, match='Restore platform application switches'):
            await _version(settings, '0028_document_parsing', downgrade=True)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(
                    text('SELECT platform_enabled FROM developer_applications WHERE id=:id'), {'id': active}
                )
                is False
            )
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
    finally:
        await engine.dispose()
