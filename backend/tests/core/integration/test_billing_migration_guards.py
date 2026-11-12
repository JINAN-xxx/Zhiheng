"""Additive counter widening and loss-preventing downgrade checks in PostgreSQL."""

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.core.integration.test_builtin_role_migration import _version
from zhiheng.modules.billing.public import CreditAdjustment, CreditService
from zhiheng.modules.identity.public import IdentityService


async def test_downgrade_rejects_credit_history_and_keeps_all_points(settings: Any) -> None:
    await _version(settings, '0013_skill_revision_history')
    engine = create_async_engine(settings.database_url.get_secret_value())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as session:
            actor = await IdentityService().create_user(
                session, username='credits-admin', password='platform-test-password', is_admin=True
            )
            await CreditService().adjust(
                session,
                actor,
                actor.id,
                CreditAdjustment(kind='grant', points='100', reason='allocation', client_request_id='allocate'),
            )
        with pytest.raises(RuntimeError, match='credit accounting data'):
            await _version(settings, '0011_builtin_admin_role', downgrade=True)
        async with engine.connect() as connection:
            assert await connection.scalar(text('SELECT allocated_points FROM billing_accounts')) == Decimal('100')
            assert await connection.scalar(text('SELECT count(*) FROM billing_ledger')) == 1
            assert (
                await connection.scalar(text('SELECT version_num FROM alembic_version'))
                == '0013_skill_revision_history'
            )
    finally:
        await engine.dispose()


async def test_bigint_counters_preserve_values_and_block_unsafe_narrowing(settings: Any) -> None:
    await _version(settings, '0011_builtin_admin_role')
    engine = create_async_engine(settings.database_url.get_secret_value())
    user_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO identity_users(id,username,password_hash,is_admin,is_active) VALUES (:id,'old-meter','historical-password-hash',false,true)"
                ),
                {'id': user_id},
            )
            await connection.execute(
                text(
                    'INSERT INTO developer_accounts(user_id,limit_tokens,used_tokens,reserved_tokens) VALUES (:id,2147483647,2147483647,0)'
                ),
                {'id': user_id},
            )
        await _version(settings, '0013_skill_revision_history')
        async with engine.begin() as connection:
            columns = (
                await connection.execute(
                    text(
                        "SELECT table_name,column_name,data_type FROM information_schema.columns WHERE (table_name='developer_accounts' AND column_name IN ('limit_tokens','used_tokens','reserved_tokens')) OR (table_name='developer_minute_buckets' AND column_name='tokens')"
                    )
                )
            ).all()
            assert len(columns) == 4 and all(row.data_type == 'bigint' for row in columns)
            before = (
                await connection.execute(
                    text('SELECT limit_tokens,used_tokens,reserved_tokens FROM developer_accounts')
                )
            ).one()
            assert before == (2147483647, 2147483647, 0)
            await connection.execute(text('UPDATE developer_accounts SET used_tokens=2147483648'))
        with pytest.raises(RuntimeError, match='meter counters'):
            await _version(settings, '0011_builtin_admin_role', downgrade=True)
        async with engine.begin() as connection:
            assert await connection.scalar(text('SELECT used_tokens FROM developer_accounts')) == 2147483648
            assert (
                await connection.scalar(text('SELECT version_num FROM alembic_version'))
                == '0013_skill_revision_history'
            )
            await connection.execute(text('UPDATE developer_accounts SET used_tokens=7'))
        await _version(settings, '0011_builtin_admin_role', downgrade=True)
        # DDL ran through Alembic's connection; use a fresh asyncpg statement cache.
        await engine.dispose()
        async with engine.connect() as connection:
            assert (
                await connection.execute(
                    text('SELECT limit_tokens,used_tokens,reserved_tokens FROM developer_accounts')
                )
            ).one() == (2147483647, 7, 0)
            assert (
                await connection.scalar(
                    text(
                        "SELECT data_type FROM information_schema.columns WHERE table_name='developer_accounts' AND column_name='used_tokens'"
                    )
                )
                == 'integer'
            )
            assert await connection.scalar(text("SELECT to_regclass('billing_accounts')")) is None
    finally:
        await engine.dispose()
