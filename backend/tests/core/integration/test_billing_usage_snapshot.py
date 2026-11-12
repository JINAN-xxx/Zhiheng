"""Additional real-meter evidence retained with the frozen points price."""

import asyncio
from decimal import Decimal
from importlib import import_module
from typing import Any

from sqlalchemy import event, text

from tests.core.integration.test_platform_credits import fund, reserve
from zhiheng.modules.billing.public import lock_billing_admission

pytest_plugins = ('tests.core.integration.test_platform_credits',)


async def test_known_partial_usage_is_preserved_and_settlement_can_be_recomputed(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    factory, service, c, actor, _, _, *_ = credit_env
    hold = await reserve(credit_env)
    async with factory.begin() as session:
        unknown = await service.settle(session, hold.run_id, input_tokens=1000, cache_tokens=400, reasoning_tokens=10)
        assert unknown.input_tokens == 1000 and unknown.output_tokens is None and unknown.cache_tokens == 400
        snapshot = await session.scalar(
            text('SELECT usage_snapshot FROM billing_reservations WHERE run_id=:id'), {'id': hold.run_id}
        )
        assert snapshot == {
            'input_tokens': 1000,
            'output_tokens': None,
            'cache_tokens': 400,
            'search_units': None,
            'reasoning_tokens': 10,
        }
    async with factory.begin() as session:
        result = await service.reconcile(
            session,
            actor,
            hold.run_id,
            c.CreditReconcile(
                expected_version=unknown.version,
                input_tokens=1000,
                output_tokens=200,
                cache_tokens=400,
                reason='confirmed usage',
                client_request_id='confirm',
            ),
        )
        assert result.charged_points == Decimal('0.17')
        assert result.input_tokens == 1000 and result.output_tokens == 200 and result.cache_tokens == 400
        assert result.reasoning_tokens == 10
        snapshot = await session.scalar(
            text('SELECT usage_snapshot FROM billing_reservations WHERE run_id=:id'), {'id': hold.run_id}
        )
        assert snapshot['input_tokens'] == result.input_tokens and snapshot['output_tokens'] == result.output_tokens


async def test_search_unit_settlement_keeps_missing_token_counts_optional(credit_env: tuple[Any, ...]) -> None:
    await fund(credit_env)
    factory, service, c, actor, _, model, *_ = credit_env
    async with factory.begin() as session:
        await service.create_rate(
            session,
            actor,
            model,
            c.CreditRateCreate(
                billing_mode='search_unit', search_points='0.2', reason='search', client_request_id='search'
            ),
            model_type='rerank',
        )
    hold = await reserve(credit_env, estimated_search_units=3)
    async with factory.begin() as session:
        result = await service.settle(session, hold.run_id, search_units=2)
        assert result.status == 'settled' and result.charged_points == Decimal('0.4')
        assert result.search_units == 2 and result.input_tokens is None and result.output_tokens is None
        assert result.billing_mode == 'search_unit'


async def test_state_refreshes_committed_mode_with_a_warm_session(credit_env: tuple[Any, ...]) -> None:
    factory, service, c, actor, *_ = credit_env
    orm = import_module('zhiheng.modules.billing.orm')
    async with factory.begin() as session:
        await service.set_state(
            session,
            actor,
            c.CreditStatePatch(enabled=False, expected_version=0, reason='initialize', client_request_id='initialize'),
        )
    async with factory() as warm:
        retained = await warm.get(orm.CreditControl, 1)
        assert retained is not None and not (await service.state(warm)).enabled
        async with factory.begin() as other:
            await service.set_state(
                other,
                actor,
                c.CreditStatePatch(enabled=True, expected_version=1, reason='enable', client_request_id='enable'),
            )
        current = await service.state(warm)
        assert current.enabled and current.version == 2


async def test_direct_core_mode_switch_uses_the_admission_gate(credit_env: tuple[Any, ...]) -> None:
    factory, service, c, actor, *_ = credit_env
    observed = asyncio.Event()
    checked_while_admitting: list[bool] = []
    admitting = True

    async def check(session: Any, enabled: bool) -> None:
        checked_while_admitting.append(admitting)
        observed.set()

    def observe(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
        if 'pg_advisory_xact_lock(' in statement and 'zhiheng:billing:mode' in str(parameters):
            observed.set()

    async def switch() -> Any:
        async with factory.begin() as session:
            return await service.set_state(
                session,
                actor,
                c.CreditStatePatch(enabled=True, expected_version=0, reason='enable', client_request_id='enable'),
                before_change=check,
            )

    engine = factory.kw['bind']
    event.listen(engine.sync_engine, 'before_cursor_execute', observe)
    task = None
    try:
        async with factory() as holder:
            await lock_billing_admission(holder)
            task = asyncio.create_task(switch())
            await asyncio.wait_for(observed.wait(), 10)
            admitting = False
            await holder.rollback()
            assert (await asyncio.wait_for(task, 10)).enabled
        assert checked_while_admitting == [False]
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        event.remove(engine.sync_engine, 'before_cursor_execute', observe)
