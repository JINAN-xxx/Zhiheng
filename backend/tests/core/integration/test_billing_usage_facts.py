"""Confirmed meter facts cannot be erased or rewritten by reconciliation."""

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from tests.core.integration.test_billing_http import mutation, point_rate
from tests.core.integration.test_platform_credits import fund, reserve
from zhiheng.modules.billing.public import CreditReconcile
from zhiheng.shared.errors import AppError

pytest_plugins = ('tests.core.integration.test_platform_credits',)


@pytest.mark.parametrize(
    'known',
    [
        {'input_tokens': 1000},
        {'output_tokens': 200},
        {'cache_tokens': 400},
        {'search_units': 3},
        {'reasoning_tokens': 10},
    ],
)
async def test_core_rejection_cannot_erase_any_confirmed_positive_usage(
    credit_env: tuple[Any, ...],
    known: dict[str, int],
) -> None:
    await fund(credit_env)
    factory, service, c, actor, user, *_ = credit_env
    hold = await reserve(credit_env)
    async with factory.begin() as session:
        unknown = await service.settle(session, hold.run_id, **known)
        before = await service.summary(session, user)
    async with factory.begin() as session:
        with pytest.raises(AppError) as conflict:
            await service.reconcile(
                session,
                actor,
                hold.run_id,
                c.CreditReconcile(
                    expected_version=unknown.version,
                    rejected_before_execution=True,
                    reason='claimed no execution',
                    client_request_id='reject',
                ),
            )
        assert conflict.value.code == 'usage_conflict' and conflict.value.status_code == 409
    async with factory() as session:
        assert (await service.summary(session, user)) == before
        stored = await session.scalar(
            text('SELECT usage_snapshot FROM billing_reservations WHERE run_id=:id'), {'id': hold.run_id}
        )
        assert all(stored[name] == value for name, value in known.items())
        assert (await service.reservations(session, actor, user_id=user))['items'][0]['status'] == 'unknown'


async def test_core_late_settle_rejection_cannot_claim_known_usage_never_executed(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    factory, service, _, _, user, *_ = credit_env
    hold = await reserve(credit_env)
    async with factory.begin() as session:
        unknown = await service.settle(session, hold.run_id, input_tokens=1000)
    async with factory.begin() as session:
        with pytest.raises(AppError) as conflict:
            await service.settle(session, hold.run_id, rejected_before_execution=True)
        assert conflict.value.code == 'usage_conflict'
    async with factory() as session:
        assert (await service.summary(session, user)).reserved_points == Decimal('0.6')
        assert (
            await session.scalar(
                text('SELECT input_tokens FROM billing_reservations WHERE run_id=:id'), {'id': hold.run_id}
            )
            == unknown.input_tokens
        )


@pytest.mark.parametrize(
    ('known', 'provided'),
    [
        ({'input_tokens': 1000}, {'input_tokens': 0, 'output_tokens': 0}),
        ({'input_tokens': 0}, {'input_tokens': 1, 'output_tokens': 0}),
        ({'output_tokens': 200}, {'input_tokens': 1000, 'output_tokens': 0}),
        ({'output_tokens': 0}, {'input_tokens': 1000, 'output_tokens': 1}),
        ({'input_tokens': 1000, 'cache_tokens': 0}, {'input_tokens': 1000, 'output_tokens': 200, 'cache_tokens': 400}),
        ({'input_tokens': 1000, 'cache_tokens': 400}, {'input_tokens': 1000, 'output_tokens': 200, 'cache_tokens': 0}),
        ({'search_units': 3}, {'input_tokens': 1000, 'output_tokens': 200, 'search_units': 2}),
    ],
)
async def test_core_reconciliation_only_fills_unknown_counters(
    credit_env: tuple[Any, ...],
    known: dict[str, int],
    provided: dict[str, int],
) -> None:
    await fund(credit_env)
    factory, service, c, actor, user, *_ = credit_env
    hold = await reserve(credit_env)
    async with factory.begin() as session:
        unknown = await service.settle(session, hold.run_id, **known)
        before = await service.summary(session, user)
    async with factory.begin() as session:
        with pytest.raises(AppError) as conflict:
            await service.reconcile(
                session,
                actor,
                hold.run_id,
                c.CreditReconcile(
                    expected_version=unknown.version,
                    reason='attempted overwrite',
                    client_request_id='rewrite',
                    **provided,
                ),
            )
        assert conflict.value.code == 'usage_conflict'
    async with factory() as session:
        assert (await service.summary(session, user)) == before
        snapshot = await session.scalar(
            text('SELECT usage_snapshot FROM billing_reservations WHERE run_id=:id'), {'id': hold.run_id}
        )
        assert all(snapshot[name] == value for name, value in known.items())


async def test_core_known_values_can_be_replayed_and_missing_values_can_be_completed(
    credit_env: tuple[Any, ...],
) -> None:
    await fund(credit_env)
    factory, service, c, actor, user, *_ = credit_env
    hold = await reserve(credit_env)
    async with factory.begin() as session:
        unknown = await service.settle(session, hold.run_id, input_tokens=1000, cache_tokens=0, reasoning_tokens=0)
    data = c.CreditReconcile(
        expected_version=unknown.version,
        input_tokens=1000,
        cache_tokens=0,
        output_tokens=200,
        reason='complete missing output',
        client_request_id='complete',
    )
    async with factory.begin() as session:
        final = await service.reconcile(session, actor, hold.run_id, data)
        assert final.charged_points == Decimal('0.2') and final.reasoning_tokens == 0
    async with factory.begin() as session:
        assert await service.reconcile(session, actor, hold.run_id, data) == final
        assert (await service.settle(session, hold.run_id, input_tokens=1, output_tokens=0)).charged_points == Decimal(
            '0.2'
        )
        with pytest.raises(AppError) as conflict:
            await service.settle(session, hold.run_id, rejected_before_execution=True)
        assert conflict.value.code == 'usage_conflict'
        assert (await service.summary(session, user)).used_points == Decimal('0.2')


async def test_core_confirmed_non_execution_can_release_when_no_positive_fact_exists(
    credit_env: tuple[Any, ...],
) -> None:
    await fund(credit_env)
    factory, service, c, actor, user, *_ = credit_env
    hold = await reserve(credit_env)
    async with factory.begin() as session:
        unknown = await service.settle(session, hold.run_id, input_tokens=0, cache_tokens=0)
    async with factory.begin() as session:
        final = await service.reconcile(
            session,
            actor,
            hold.run_id,
            c.CreditReconcile(
                expected_version=unknown.version,
                rejected_before_execution=True,
                reason='provider confirmed no execution',
                client_request_id='reject',
            ),
        )
        assert final.status == 'settled' and final.charged_points == 0
        assert (
            final.input_tokens
            == final.output_tokens
            == final.cache_tokens
            == final.search_units
            == final.reasoning_tokens
            == 0
        )
        balance = await service.summary(session, user)
        assert balance.available_points == Decimal('100') and balance.reserved_points == 0


@pytest.mark.parametrize(
    ('known', 'provided'),
    [
        ({'input_tokens': 1000}, {'rejected_before_execution': True}),
        ({'input_tokens': 1000}, {'input_tokens': 0, 'output_tokens': 0}),
        ({'input_tokens': 0}, {'input_tokens': 1, 'output_tokens': 0}),
        ({'input_tokens': 1000, 'cache_tokens': 0}, {'input_tokens': 1000, 'output_tokens': 200, 'cache_tokens': 400}),
    ],
)
async def test_http_core_conflict_rolls_back_legacy_settlement_and_both_meters(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    known: dict[str, int],
    provided: dict[str, Any],
) -> None:
    await point_rate(admin, model_id)
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    assert (
        await admin.post(f'/api/billing/accounts/{owner}/adjust', json=mutation(kind='grant', points='100'))
    ).status_code == 200
    assert (await admin.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0))).status_code == 200
    container, run_id = app.state.container, uuid4()
    async with container.database.sessions.begin() as session:
        await container.usage.reserve(
            session,
            user_id=owner,
            run_id=run_id,
            model_id=UUID(model_id),
            source='test',
            tokens=2000,
            token_limit=10000,
            estimated_input_tokens=1000,
            max_output_tokens=1000,
        )
        # Each owner received a different amount of confirmed diagnostic data.
        await container.metering.settle(session, run_id)
        await container.credits.settle(session, run_id, **known)
    before_credit = (await admin.get('/api/billing/me')).json()
    before_usage = (await admin.get('/api/billing/me/usage')).json()['items'][0]
    async with container.database.sessions() as session:
        before_legacy = (
            await session.execute(
                text(
                    'SELECT status,input_tokens,output_tokens,cache_tokens,reasoning_tokens,search_units,charged_tokens FROM developer_invocations WHERE run_id=:id'
                ),
                {'id': run_id},
            )
        ).one()
        before_account = (
            await session.execute(
                text('SELECT used_tokens,reserved_tokens FROM developer_accounts WHERE user_id=:id'), {'id': owner}
            )
        ).one()
    body = mutation(expected_version=before_usage['version'], **provided)
    response = await admin.post(f'/api/billing/usage/{run_id}/reconcile', json=body)
    assert response.status_code == 409 and response.json()['error']['code'] == 'usage_conflict', response.text
    assert (await admin.get('/api/billing/me')).json() == before_credit
    assert (await admin.get('/api/billing/me/usage')).json()['items'][0] == before_usage
    # A caller that handles the rejection may still commit its surrounding transaction.
    # The facade's own savepoint must restore the already-settled historical meter.
    async with container.database.sessions.begin() as session:
        actor = await container.identity.principal_for_user(session, owner)
        with pytest.raises(AppError) as conflict:
            await container.usage.reconcile(
                session,
                actor,
                run_id,
                CreditReconcile(
                    **{
                        **body,
                        'client_request_id': uuid4().hex,
                    }
                ),
            )
        assert conflict.value.code == 'usage_conflict'
        assert await session.scalar(text('SELECT 1')) == 1
    assert (await admin.get('/api/billing/me')).json() == before_credit
    assert (await admin.get('/api/billing/me/usage')).json()['items'][0] == before_usage
    async with container.database.sessions() as session:
        assert (
            await session.execute(
                text(
                    'SELECT status,input_tokens,output_tokens,cache_tokens,reasoning_tokens,search_units,charged_tokens FROM developer_invocations WHERE run_id=:id'
                ),
                {'id': run_id},
            )
        ).one() == before_legacy
        assert (
            await session.execute(
                text('SELECT used_tokens,reserved_tokens FROM developer_accounts WHERE user_id=:id'), {'id': owner}
            )
        ).one() == before_account
        assert await session.scalar(text("SELECT count(*) FROM billing_commands WHERE kind='reconcile'")) == 0
