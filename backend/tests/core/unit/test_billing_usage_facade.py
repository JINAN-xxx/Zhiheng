from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zhiheng.modules.billing.usage import UsageService
from zhiheng.modules.developer.contracts import LegacyReservation, UsageOutcome


class Session:
    def __init__(self) -> None:
        self.gated = False

    async def execute(self, query: object, parameters: object) -> None:
        self.gated = True

    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        yield


def services(enabled: bool) -> tuple[UsageService, SimpleNamespace, SimpleNamespace]:
    legacy = SimpleNamespace(
        reserve=AsyncMock(return_value=LegacyReservation(enabled, uuid4(), Decimal('1.5'))),
        require_reservation=AsyncMock(return_value=LegacyReservation(enabled)),
        settle=AsyncMock(return_value=UsageOutcome(status='known', points_enforced=enabled)),
        reconcile=AsyncMock(),
    )
    credit_service = SimpleNamespace(
        state=AsyncMock(return_value=SimpleNamespace(enabled=enabled)),
        reserve=AsyncMock(),
        require_reservation=AsyncMock(),
        settle=AsyncMock(),
        reconcile=AsyncMock(),
    )
    return UsageService(credit_service, legacy), credit_service, legacy


@pytest.mark.parametrize('enabled', [False, True])
async def test_mode_is_frozen_during_admission_and_uses_separate_estimates(enabled: bool) -> None:
    service, credit_service, legacy = services(enabled)
    session, user, run, model = Session(), uuid4(), uuid4(), uuid4()
    await service.reserve(
        session,
        user_id=user,
        run_id=run,
        model_id=model,
        source='chat',
        tokens=2000,
        token_limit=100,
        estimated_input_tokens=1000,
        max_output_tokens=1000,
    )
    assert legacy.reserve.await_args.kwargs['enforce_token_limit'] is (not enabled)
    assert legacy.reserve.await_args.kwargs['points_enforced'] is enabled
    if enabled:
        credit_service.reserve.assert_awaited_once_with(
            session,
            user_id=user,
            run_id=run,
            model_id=model,
            source='chat',
            operation='chat',
            estimated_input_tokens=1000,
            max_output_tokens=1000,
            estimated_search_units=0,
            key_id=legacy.reserve.return_value.key_id,
            key_limit=Decimal('1.5'),
        )
    else:
        credit_service.reserve.assert_not_awaited()


@pytest.mark.parametrize('enabled', [False, True])
async def test_execution_requires_points_only_for_the_persisted_scheme(enabled: bool) -> None:
    service, credit_service, legacy = services(enabled)
    session, run, user, model = Session(), uuid4(), uuid4(), uuid4()
    await service.require_reservation(session, run, user, model)
    legacy.require_reservation.assert_awaited_once_with(session, run, user, model)
    assert credit_service.require_reservation.await_count == int(enabled)
    credit_service.state.assert_not_awaited()


@pytest.mark.parametrize('enabled', [False, True])
async def test_settlement_uses_admission_scheme_even_after_mode_changes(enabled: bool) -> None:
    service, credit_service, _ = services(enabled)
    credit_service.state.return_value.enabled = not enabled
    session, run = Session(), uuid4()
    result = await service.settle(session, run, input_tokens=1000, output_tokens=200)
    assert result.status == 'known'
    assert credit_service.settle.await_count == int(enabled)
    credit_service.state.assert_not_awaited()


async def test_reconciliation_passes_the_real_actor_to_both_ledgers() -> None:
    from zhiheng.modules.billing.contracts import CreditReconcile
    from zhiheng.shared.principal import Principal

    service, credit_service, legacy = services(True)
    actor, run = Principal(uuid4(), 'admin', True), uuid4()
    body = CreditReconcile(
        expected_version=2,
        reason='confirmed usage',
        client_request_id='confirmation',
        input_tokens=1000,
        output_tokens=200,
    )
    session = Session()
    await service.reconcile(session, actor, run, body)
    legacy.reconcile.assert_awaited_once_with(
        session,
        run,
        input_tokens=1000,
        output_tokens=200,
        cache_tokens=None,
        search_units=None,
        rejected_before_execution=False,
    )
    credit_service.reconcile.assert_awaited_once_with(session, actor, run, body)


async def test_missing_money_rate_is_allowed_only_when_required_in_points_mode() -> None:
    from zhiheng.shared.errors import AppError

    service, credit_service, _ = services(False)
    models = SimpleNamespace(latest_rate=AsyncMock(side_effect=AppError('model_not_found', 'missing', 404)))
    with pytest.raises(AppError):
        await service.money_rate(Session(), models, uuid4(), required=True)
    credit_service.state.return_value.enabled = True
    assert await service.money_rate(Session(), models, uuid4(), required=True) is None


async def test_disable_between_price_lookup_and_admission_cannot_bypass_old_public_price_requirement() -> None:
    from zhiheng.shared.errors import AppError

    service, _, legacy = services(False)
    with pytest.raises(AppError) as rejected:
        await service.reserve(
            Session(),
            user_id=uuid4(),
            run_id=uuid4(),
            model_id=uuid4(),
            source='public',
            tokens=20,
            token_limit=100,
            key_id=uuid4(),
            rate=None,
            estimated_input_tokens=10,
            max_output_tokens=10,
        )
    assert rejected.value.code == 'model_not_found'
    legacy.reserve.assert_not_awaited()


async def test_rerank_search_billing_is_independent_of_unknown_token_statistics() -> None:
    service, credit_service, legacy = services(True)
    legacy.settle.return_value = UsageOutcome(status='unknown', points_enforced=True)
    await service.settle(Session(), uuid4(), search_units=2)
    assert credit_service.settle.await_args.kwargs['search_units'] == 2


async def test_billing_mode_is_read_only_after_the_shared_transaction_gate() -> None:
    service, credit_service, _ = services(True)
    session = Session()

    async def state(_session: object) -> SimpleNamespace:
        assert session.gated
        return SimpleNamespace(enabled=True)

    credit_service.state.side_effect = state
    assert await service.points_enabled(session)
