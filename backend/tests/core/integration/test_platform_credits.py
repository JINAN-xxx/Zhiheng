"""Credit invariants against disposable PostgreSQL databases and real row locks."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Column, MetaData, Table, Uuid, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


@pytest_asyncio.fixture
async def credit_env() -> AsyncIterator[tuple[Any, ...]]:
    path = Path(__file__).parents[3] / 'src/zhiheng/modules/billing/service.py'
    assert path.is_file(), 'Platform credit service is not implemented'
    url = os.getenv('ZHIHENG_TEST_DATABASE_URL')
    if not url:
        pytest.fail('A disposable PostgreSQL runner is required; this suite must never silently skip')
    database = f'credits_test_{uuid4().hex}'
    admin_engine = create_async_engine(url, isolation_level='AUTOCOMMIT')
    async with admin_engine.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database}"'))
    engine = create_async_engine(make_url(url).set(database=database))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service_module = import_module('zhiheng.modules.billing.service')
    orm = import_module('zhiheng.modules.billing.orm')
    for owner in ('identity', 'models', 'developer'):
        import_module(f'zhiheng.modules.{owner}.orm')
    c = import_module('zhiheng.modules.billing.contracts')
    actor = Principal(uuid4(), 'credit-admin', True)
    user_id, model_id, key_a, key_b = uuid4(), uuid4(), uuid4(), uuid4()
    try:
        async with engine.begin() as connection:
            parents = MetaData()
            for name in ('identity_users', 'models', 'developer_keys'):
                Table(name, parents, Column('id', Uuid, primary_key=True))
            await connection.run_sync(parents.create_all)
            for table in orm.schema_tables():
                await connection.run_sync(table.create)
            await connection.execute(
                text('INSERT INTO identity_users(id) VALUES (:u),(:a)'), {'u': user_id, 'a': actor.id}
            )
            await connection.execute(text('INSERT INTO models(id) VALUES (:m)'), {'m': model_id})
            await connection.execute(text('INSERT INTO developer_keys(id) VALUES (:a),(:b)'), {'a': key_a, 'b': key_b})
        yield factory, service_module.CreditService(), c, actor, user_id, model_id, key_a, key_b
    finally:
        await engine.dispose()
        async with admin_engine.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database}" WITH (FORCE)'))
        await admin_engine.dispose()


async def fund(credit_env: tuple[Any, ...], points: str = '100') -> None:
    factory, service, c, actor, user, model, *_ = credit_env
    async with factory.begin() as session:
        await service.adjust(
            session,
            actor,
            user,
            c.CreditAdjustment(
                kind='grant', points=points, reason='test allocation', client_request_id='initial-grant'
            ),
        )
        await service.create_rate(
            session,
            actor,
            model,
            c.CreditRateCreate(
                billing_mode='token',
                input_points='100',
                output_points='500',
                cache_points='25',
                reason='test rate',
                client_request_id='initial-rate',
            ),
            model_type='chat',
        )


async def reserve(credit_env: tuple[Any, ...], run_id: UUID | None = None, **kwargs: Any) -> Any:
    factory, service, _, _, user, model, *_ = credit_env
    async with factory.begin() as session:
        return await service.reserve(
            session,
            user_id=user,
            model_id=model,
            run_id=run_id or uuid4(),
            source='test',
            estimated_input_tokens=1000,
            max_output_tokens=1000,
            **kwargs,
        )


async def test_zero_default_and_platform_mode_require_super_admin(credit_env: tuple[Any, ...]) -> None:
    factory, service, c, actor, user, *_ = credit_env
    async with factory.begin() as session:
        state = await service.state(session)
        summary = await service.summary(session, user)
        assert state.model_dump() == {'enabled': False, 'version': 0}
        assert summary.available_points == Decimal(0) and summary.version == 0 and summary.enabled
        writer = Principal(actor.id, 'writer', False, frozenset({'ai:credit_env:write'}))
        with pytest.raises(AppError) as denied:
            await service.set_state(
                session,
                writer,
                c.CreditStatePatch(enabled=True, expected_version=0, reason='enable', client_request_id='enable'),
            )
        assert denied.value.status_code == 403


async def test_grant_idempotency_and_payload_conflict_with_real_concurrency(credit_env: tuple[Any, ...]) -> None:
    factory, service, c, actor, user, *_ = credit_env
    body = c.CreditAdjustment(kind='grant', points='100', reason='allocation', client_request_id='same-grant')

    async def grant() -> Any:
        async with factory.begin() as session:
            return await service.adjust(session, actor, user, body)

    await asyncio.wait_for(asyncio.gather(grant(), grant(), grant()), 10)
    async with factory.begin() as session:
        assert (await service.summary(session, user)).allocated_points == Decimal(100)
        with pytest.raises(AppError) as conflict:
            await service.adjust(session, actor, user, body.model_copy(update={'points': Decimal(101)}))
        assert conflict.value.status_code == 409


async def test_example_reserves_point_six_and_charges_point_two(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    factory, service, _, _, user, *_ = credit_env
    reservation = await reserve(credit_env)
    assert reservation.reserved_points == Decimal('0.6')
    async with factory.begin() as session:
        result = await service.settle(session, reservation.run_id, input_tokens=1000, output_tokens=200)
        repeated = await service.settle(session, reservation.run_id, input_tokens=1, output_tokens=0)
        assert result.charged_points == repeated.charged_points == Decimal('0.2')
        summary = await service.summary(session, user)
        assert summary.available_points == Decimal('99.8') and summary.reserved_points == 0


async def test_concurrent_reservations_cannot_exceed_wallet(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env, '1')

    async def attempt() -> str:
        try:
            await reserve(credit_env)
            return 'ok'
        except AppError as exc:
            return exc.code

    results = await asyncio.wait_for(asyncio.gather(attempt(), attempt()), 10)
    assert results.count('ok') == 1 and results.count('credits_insufficient') == 1


async def test_concurrent_reclaims_do_not_touch_reserved_funds(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env, '1')
    await reserve(credit_env)
    factory, service, c, actor, user, *_ = credit_env

    async def reclaim() -> str:
        try:
            async with factory.begin() as session:
                await service.adjust(
                    session,
                    actor,
                    user,
                    c.CreditAdjustment(kind='reclaim', points='0.3', reason='unused', client_request_id=uuid4().hex),
                )
            return 'ok'
        except AppError as exc:
            return exc.code

    results = await asyncio.wait_for(asyncio.gather(reclaim(), reclaim()), 10)
    assert results.count('ok') == 1 and results.count('credits_insufficient') == 1


async def test_locked_price_snapshot_survives_new_rate(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    first = await reserve(credit_env)
    factory, service, c, actor, _, model, *_ = credit_env
    async with factory.begin() as session:
        await service.create_rate(
            session,
            actor,
            model,
            c.CreditRateCreate(
                billing_mode='token',
                input_points='1000',
                output_points='5000',
                reason='new price',
                client_request_id='rate-two',
            ),
            model_type='chat',
        )
        settled = await service.settle(session, first.run_id, input_tokens=1000, output_tokens=200)
        assert settled.charged_points == Decimal('0.2') and settled.rate_version == 1
    assert (await reserve(credit_env)).reserved_points == Decimal('6')


async def test_unknown_keeps_reservation_until_versioned_admin_reconciliation(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    row = await reserve(credit_env)
    factory, service, c, actor, user, *_ = credit_env
    async with factory.begin() as session:
        unknown = await service.settle(session, row.run_id, input_tokens=1000)
        assert unknown.status == 'unknown'
        assert (await service.summary(session, user)).reserved_points == Decimal('0.6')
    body = c.CreditReconcile(
        expected_version=unknown.version,
        reason='provider confirmed',
        client_request_id='reconcile-one',
        input_tokens=1000,
        output_tokens=200,
    )
    async with factory.begin() as session:
        result = await service.reconcile(session, actor, row.run_id, body)
        assert result.status == 'settled' and result.charged_points == Decimal('0.2')
    async with factory.begin() as session:
        assert (await service.reconcile(session, actor, row.run_id, body)).charged_points == Decimal('0.2')


async def test_pause_blocks_reserve_but_existing_settlement_completes(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    row = await reserve(credit_env)
    factory, service, c, actor, user, *_ = credit_env
    async with factory.begin() as session:
        summary = await service.summary(session, user)
        await service.set_account_state(
            session,
            actor,
            user,
            c.CreditAccountStatePatch(
                enabled=False, expected_version=summary.version, reason='pause', client_request_id='pause'
            ),
        )
    with pytest.raises(AppError) as paused:
        await reserve(credit_env)
    assert paused.value.code == 'credits_paused'
    async with factory.begin() as session:
        assert (await service.settle(session, row.run_id, input_tokens=1000, output_tokens=200)).status == 'settled'


async def test_actual_overrun_is_recorded_and_blocks_next_call(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env, '1')
    row = await reserve(credit_env)
    factory, service, _, _, user, *_ = credit_env
    async with factory.begin() as session:
        await service.settle(session, row.run_id, input_tokens=1000, output_tokens=4000)
        assert (await service.summary(session, user)).available_points == Decimal('-1.1')
    with pytest.raises(AppError) as insufficient:
        await reserve(credit_env)
    assert insufficient.value.code == 'credits_insufficient'


async def test_keys_share_parent_wallet_and_have_independent_month_limits(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env, '1')
    *_, key_a, key_b = credit_env
    row = await reserve(credit_env, key_id=key_a, key_limit=Decimal('0.7'))
    with pytest.raises(AppError) as key_limit:
        await reserve(credit_env, key_id=key_a, key_limit=Decimal('0.7'))
    assert key_limit.value.code == 'key_points_limit_exceeded'
    with pytest.raises(AppError) as wallet:
        await reserve(credit_env, key_id=key_b, key_limit=None)
    assert wallet.value.code == 'credits_insufficient'
    factory, service, *_ = credit_env
    async with factory.begin() as session:
        await service.settle(session, row.run_id, input_tokens=1000, output_tokens=200)
    assert (await reserve(credit_env, key_id=key_b, key_limit=None)).reserved_points == Decimal('0.6')


async def test_zero_key_limit_rejects_even_free_calls(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    factory, service, c, actor, _, model, key_a, _ = credit_env
    async with factory.begin() as session:
        await service.create_rate(
            session,
            actor,
            model,
            c.CreditRateCreate(billing_mode='token', is_free=True, reason='free', client_request_id='free'),
            model_type='chat',
        )
    with pytest.raises(AppError) as denied:
        await reserve(credit_env, key_id=key_a, key_limit=Decimal(0))
    assert denied.value.code == 'key_points_limit_exceeded'


async def test_reservation_idempotency_does_not_double_hold_and_conflicts_on_payload(
    credit_env: tuple[Any, ...],
) -> None:
    await fund(credit_env)
    run_id = uuid4()
    await reserve(credit_env, run_id)
    await reserve(credit_env, run_id)
    factory, service, _, _, user, model, *_ = credit_env
    async with factory.begin() as session:
        assert (await service.summary(session, user)).reserved_points == Decimal('0.6')
        with pytest.raises(AppError) as conflict:
            await service.reserve(
                session,
                user_id=user,
                run_id=run_id,
                model_id=model,
                source='other',
                estimated_input_tokens=1000,
                max_output_tokens=1000,
            )
        assert conflict.value.status_code == 409


async def test_key_month_is_shanghai_time_and_settlement_stays_in_original_month(
    credit_env: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    await fund(credit_env)
    module = import_module('zhiheng.modules.billing.service')
    monkeypatch.setattr(module, 'utcnow', lambda: datetime(2026, 10, 31, 16, 1, tzinfo=timezone.utc))
    factory, service, _, _, _, _, key_a, _ = credit_env
    first = await reserve(credit_env, key_id=key_a, key_limit=Decimal('0.7'))
    async with factory() as session:
        assert (
            await session.scalar(
                text('SELECT key_month FROM billing_reservations WHERE run_id=:id'), {'id': first.run_id}
            )
            == '2026-11'
        )
    monkeypatch.setattr(module, 'utcnow', lambda: datetime(2026, 11, 30, 16, 1, tzinfo=timezone.utc))
    await reserve(credit_env, key_id=key_a, key_limit=Decimal('0.7'))
    async with factory.begin() as session:
        await service.settle(session, first.run_id, input_tokens=1000, output_tokens=200)
        months = (
            await session.execute(
                text('SELECT month,used_points,reserved_points FROM billing_key_months ORDER BY month')
            )
        ).all()
        assert months == [('2026-11', Decimal('0.2'), Decimal('0')), ('2026-12', Decimal('0'), Decimal('0.6'))]


async def test_admin_write_and_cross_user_history_permissions_are_enforced_in_core(credit_env: tuple[Any, ...]) -> None:
    factory, service, c, actor, user, model, *_ = credit_env
    member = Principal(actor.id, 'member', False)
    async with factory.begin() as session:
        for invoke in (
            lambda: service.adjust(
                session, member, user, c.CreditAdjustment(kind='grant', points='1', reason='x', client_request_id='x')
            ),
            lambda: service.set_account_state(
                session,
                member,
                user,
                c.CreditAccountStatePatch(enabled=False, expected_version=0, reason='x', client_request_id='x'),
            ),
            lambda: service.create_rate(
                session,
                member,
                model,
                c.CreditRateCreate(billing_mode='token', is_free=True, reason='x', client_request_id='x'),
                model_type='chat',
            ),
            lambda: service.rates(session, member, model),
            lambda: service.ledger(session, member, user_id=user),
            lambda: service.reservations(session, member, user_id=user),
        ):
            with pytest.raises(AppError) as denied:
                await invoke()
            assert denied.value.status_code == 403
        assert (await service.ledger(session, member))['total'] == 0


async def test_migration_adds_zero_default_credit_scheme_without_changing_old_units(
    credit_env: tuple[Any, ...],
) -> None:
    from importlib.util import module_from_spec, spec_from_file_location

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    factory, _, _, _, _user, _, key_a, _ = credit_env
    orm = import_module('zhiheng.modules.billing.orm')
    migration_file = Path(__file__).parents[3] / 'migrations/versions/0012_platform_credits.py'
    assert migration_file.is_file(), 'The additive platform credit migration is not implemented'
    module_spec = spec_from_file_location('platform_credits_migration', migration_file)
    module = module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    async with factory.begin() as session:
        connection = await session.connection()
        for table in reversed(orm.schema_tables()):
            await connection.run_sync(table.drop)
        await session.execute(text('ALTER TABLE developer_keys ADD COLUMN monthly_budget NUMERIC(30,12)'))
        await session.execute(text('UPDATE developer_keys SET monthly_budget=12.345 WHERE id=:id'), {'id': key_a})
        await session.execute(
            text(
                'CREATE TABLE developer_invocations(id UUID PRIMARY KEY,input_tokens INTEGER,billable_cost NUMERIC(30,12))'
            )
        )
        await session.execute(text('INSERT INTO developer_invocations VALUES (:id,1234,0.00123)'), {'id': uuid4()})
        await session.execute(
            text(
                'CREATE TABLE developer_accounts(user_id UUID PRIMARY KEY,limit_tokens INTEGER NOT NULL,used_tokens INTEGER NOT NULL,reserved_tokens INTEGER NOT NULL)'
            )
        )
        await session.execute(
            text('CREATE TABLE developer_minute_buckets(key_id UUID PRIMARY KEY,tokens INTEGER NOT NULL)')
        )
        await session.execute(text('INSERT INTO developer_accounts VALUES (:id,100000,1234,56)'), {'id': _user})
        await session.execute(text('INSERT INTO developer_minute_buckets VALUES (:id,78)'), {'id': key_a})

        def upgrade(sync_connection: Connection) -> None:
            with Operations.context(MigrationContext.configure(sync_connection)):
                module.upgrade()

        await connection.run_sync(upgrade)
        assert (await session.execute(text('SELECT enabled,version FROM billing_control'))).one() == (False, 0)
        assert await session.scalar(text('SELECT count(*) FROM billing_accounts')) == 0
        assert (
            await session.scalar(text('SELECT monthly_points_limit FROM developer_keys WHERE id=:id'), {'id': key_a})
            is None
        )
        assert await session.scalar(
            text('SELECT monthly_budget FROM developer_keys WHERE id=:id'), {'id': key_a}
        ) == Decimal('12.345')
        assert (
            await session.execute(text('SELECT input_tokens,billable_cost,points_enforced FROM developer_invocations'))
        ).one() == (1234, Decimal('0.00123'), False)
        assert (
            await session.execute(text('SELECT limit_tokens,used_tokens,reserved_tokens FROM developer_accounts'))
        ).one() == (100000, 1234, 56)
        assert await session.scalar(text('SELECT tokens FROM developer_minute_buckets')) == 78


async def test_unpriced_models_fail_and_explicit_free_models_use_zero_balance(credit_env: tuple[Any, ...]) -> None:
    factory, service, c, actor, user, model, *_ = credit_env
    with pytest.raises(AppError) as missing:
        await reserve(credit_env)
    assert missing.value.code == 'credit_rate_missing'
    async with factory.begin() as session:
        await service.create_rate(
            session,
            actor,
            model,
            c.CreditRateCreate(billing_mode='token', is_free=True, reason='free model', client_request_id='free-rate'),
            model_type='chat',
        )
    row = await reserve(credit_env)
    async with factory.begin() as session:
        await service.require_reservation(session, row.run_id, user, model)
        result = await service.settle(session, row.run_id, input_tokens=1000, output_tokens=1000)
        assert result.charged_points == Decimal(0)
        with pytest.raises(AppError):
            await service.require_reservation(session, row.run_id, user, model)
        assert (await service.summary(session, user)).available_points == Decimal(0)


async def test_rerank_search_units_and_rejection_release_unknown_holds(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    factory, service, c, actor, user, model, *_ = credit_env
    async with factory.begin() as session:
        await service.create_rate(
            session,
            actor,
            model,
            c.CreditRateCreate(
                billing_mode='search_unit', search_points='0.2', reason='rerank rate', client_request_id='rerank-rate'
            ),
            model_type='rerank',
        )
    row = await reserve(credit_env, estimated_search_units=3)
    assert row.reserved_points == Decimal('0.6')
    async with factory.begin() as session:
        result = await service.settle(session, row.run_id, search_units=2)
        assert result.charged_points == Decimal('0.4')
    unknown_row = await reserve(credit_env, estimated_search_units=3)
    async with factory.begin() as session:
        unknown = await service.settle(session, unknown_row.run_id)
    async with factory.begin() as session:
        result = await service.reconcile(
            session,
            actor,
            unknown.run_id,
            c.CreditReconcile(
                expected_version=unknown.version,
                rejected_before_execution=True,
                reason='confirmed provider rejection',
                client_request_id='rejected',
            ),
        )
        assert result.charged_points == 0
        assert (await service.summary(session, user)).available_points == Decimal('99.6')


async def test_parallel_settlement_writes_one_charge_and_balanced_ledger(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    row = await reserve(credit_env)
    factory, service, _, actor, user, *_ = credit_env

    async def settle() -> Any:
        async with factory.begin() as session:
            return await service.settle(session, row.run_id, input_tokens=1000, output_tokens=200)

    results = await asyncio.wait_for(asyncio.gather(settle(), settle(), settle()), 10)
    assert all(result.charged_points == Decimal('0.2') for result in results)
    async with factory() as session:
        entries = (await service.ledger(session, actor, user_id=user))['items']
        assert len(entries) == 3
        assert sum(Decimal(entry['available_delta']) for entry in entries) == Decimal('99.8')
        assert sum(Decimal(entry['reserved_delta']) for entry in entries) == 0
        assert (await service.summary(session, user)).used_points == Decimal('0.2')


async def test_state_versions_audit_and_idempotency(credit_env: tuple[Any, ...]) -> None:
    factory, service, c, actor, *_ = credit_env
    body = c.CreditStatePatch(
        enabled=True, expected_version=0, reason='prepared rates and grants', client_request_id='enable'
    )
    async with factory.begin() as session:
        state = await service.set_state(session, actor, body)
        assert state.enabled and state.version == 1
    async with factory.begin() as session:
        assert (await service.set_state(session, actor, body)).version == 1
        with pytest.raises(AppError) as stale:
            await service.set_state(
                session,
                actor,
                c.CreditStatePatch(
                    enabled=False, expected_version=0, reason='stale disable', client_request_id='disable'
                ),
            )
        assert stale.value.code == 'version_conflict'
        assert await session.scalar(text("SELECT count(*) FROM billing_commands WHERE kind='state'")) == 1


async def test_concurrent_key_month_holds_obey_limit_with_one_parent_wallet(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    *_, key_a, _key_b = credit_env

    async def attempt() -> str:
        try:
            await reserve(credit_env, key_id=key_a, key_limit=Decimal('1'))
            return 'ok'
        except AppError as exc:
            return exc.code

    results = await asyncio.wait_for(asyncio.gather(attempt(), attempt(), attempt()), 10)
    assert results.count('ok') == 1 and results.count('key_points_limit_exceeded') == 2
