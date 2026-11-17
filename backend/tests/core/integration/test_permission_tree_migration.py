"""Frozen 0034 history converts legacy role grants without runtime aliases."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.core.integration.test_admin_console_migration import _version
from tests.core.permission_support import grants
from zhiheng.bootstrap.settings import Settings


async def test_0034_converts_stored_grants_and_preserves_role_identity(settings: Settings) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value())
    fixtures = [
        (
            ['platform:knowledge:access', 'platform:models:access'],
            grants('platform:knowledge', 'query') | grants('platform:models'),
        ),
        (
            ['platform:knowledge:manage'],
            grants('platform:knowledge', 'create', 'update', 'delete', 'import', 'rebuild'),
        ),
        (
            ['system:users:write'],
            grants('system:users', 'create', 'update', 'delete', 'toggle', 'reset_password', 'assign_roles'),
        ),
        (
            ['ai:models:write'],
            grants('platform:models', 'config_read', 'create', 'update', 'delete', 'toggle'),
        ),
        (['system:posts:write', 'ai:skills:write'], frozenset()),
    ]
    identifiers = [uuid4() for _ in fixtures]
    try:
        await _version(engine, '0033_assistant_session_actions')
        async with engine.begin() as connection:
            for index, ((legacy, _), identifier) in enumerate(zip(fixtures, identifiers, strict=True)):
                await connection.execute(
                    text(
                        'INSERT INTO identity_roles '
                        '(id,code,name,permissions,is_active,remark,version,created_at,updated_at) '
                        'VALUES (:id,:code,:name,CAST(:permissions AS json),false,:remark,7,now(),now())'
                    ),
                    {
                        'id': identifier,
                        'code': f'historical_{index}',
                        'name': f'Existing role {index}',
                        'permissions': json.dumps(legacy),
                        'remark': 'Preserved historical description',
                    },
                )
        await _version(engine, '0034_permission_tree')
        async with engine.connect() as connection:
            for index, ((_, expected), identifier) in enumerate(zip(fixtures, identifiers, strict=True)):
                row = (
                    await connection.execute(
                        text('SELECT code,name,permissions,is_active,remark,version FROM identity_roles WHERE id=:id'),
                        {'id': identifier},
                    )
                ).one()
                assert row.permissions == sorted(expected)
                assert row.code == f'historical_{index}' and row.name == f'Existing role {index}'
                assert not row.is_active and row.remark == 'Preserved historical description'
                assert row.version == 8
                assert not any(code.startswith('ai:credentials:') for code in row.permissions)
        # Alembic must not reconvert canonical roles or increment their versions again.
        await _version(engine, '0034_permission_tree')
        async with engine.connect() as connection:
            versions = await connection.scalars(
                text('SELECT version FROM identity_roles WHERE id=ANY(:ids)'), {'ids': identifiers}
            )
            assert list(versions) == [8] * len(fixtures)
        with pytest.raises(RuntimeError, match='reviewed role-permission backup'):
            await _version(engine, '0033_assistant_session_actions', downgrade=True)
    finally:
        await engine.dispose()
