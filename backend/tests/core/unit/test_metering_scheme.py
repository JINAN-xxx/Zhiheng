from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zhiheng.modules.billing.metering import MeteringService
from zhiheng.modules.billing.orm import Invocation, UsageAccount
from zhiheng.shared.errors import AppError


class MeterSession:
    def __init__(self, *results: object) -> None:
        self.results = iter(results)
        self.added: list[object] = []

    async def execute(self, query: object) -> None:
        pass

    async def scalar(self, query: object) -> object:
        return next(self.results)

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        pass


async def test_points_scheme_preserves_token_statistics_without_old_lifetime_gate() -> None:
    user, run, model = uuid4(), uuid4(), uuid4()
    account = UsageAccount(user_id=user, limit_tokens=1, used_tokens=1, reserved_tokens=0)
    session = MeterSession(account)
    reservation = await MeteringService(AsyncMock()).reserve(
        session,
        user_id=user,
        run_id=run,
        model_id=model,
        source='chat',
        tokens=100,
        token_limit=1,
        points_enforced=True,
    )
    assert reservation.points_enforced and account.reserved_tokens == 100
    assert session.added[0].points_enforced


async def test_disabled_points_has_no_lifetime_gate_or_token_reservation() -> None:
    account = UsageAccount(user_id=uuid4(), limit_tokens=1, used_tokens=1, reserved_tokens=0)
    session = MeterSession(account)
    reservation = await MeteringService(AsyncMock()).reserve(
        session,
        user_id=account.user_id,
        run_id=uuid4(),
        model_id=uuid4(),
        source='chat',
        tokens=100,
        token_limit=1,
    )
    assert not reservation.points_enforced
    assert account.reserved_tokens == 0
    assert session.added[0].reserved_tokens == 0
    assert session.added[0].rate_snapshot is None
    assert session.added[0].reserved_cost is None


async def test_confirmed_unknown_usage_releases_old_token_hold_and_records_actual_usage() -> None:
    user, run = uuid4(), uuid4()
    invocation = Invocation(
        version=1,
        user_id=user,
        run_id=run,
        status='unknown',
        points_enforced=True,
        reserved_tokens=100,
        operation='chat',
    )
    account = UsageAccount(user_id=user, limit_tokens=1, used_tokens=0, reserved_tokens=100)
    outcome = await MeteringService(AsyncMock()).reconcile(
        MeterSession(invocation, account, None), run, input_tokens=10, output_tokens=5
    )
    assert outcome.status == 'known' and outcome.points_enforced
    assert account.reserved_tokens == 0 and account.used_tokens == 15


async def test_normal_completion_cannot_silently_reconcile_unknown_usage() -> None:
    invocation = Invocation(version=1, run_id=uuid4(), status='unknown', points_enforced=True, reserved_tokens=100)
    outcome = await MeteringService(AsyncMock()).settle(
        MeterSession(invocation), invocation.run_id, input_tokens=10, output_tokens=5
    )
    assert outcome.status == 'unknown'


async def test_rerank_money_uses_known_search_units_without_inventing_token_usage() -> None:
    invocation = Invocation(
        version=1,
        run_id=uuid4(),
        status='pending',
        points_enforced=True,
        reserved_tokens=100,
        operation='rerank',
        rate_snapshot={'billing_mode': 'search_unit', 'search_price': '0.25'},
    )
    account = UsageAccount(user_id=invocation.user_id, limit_tokens=1, used_tokens=0, reserved_tokens=100)
    outcome = await MeteringService(AsyncMock()).settle(
        MeterSession(invocation, account), invocation.run_id, search_units=2
    )
    assert outcome.status == 'unknown' and outcome.input_tokens is None and outcome.output_tokens is None
    assert invocation.billable_cost == Decimal('0.50') and invocation.charged_tokens is None


async def test_search_only_reconciliation_keeps_unknown_tokens_and_known_reasoning() -> None:
    invocation = Invocation(
        version=1,
        run_id=uuid4(),
        status='unknown',
        points_enforced=True,
        reserved_tokens=100,
        operation='rerank',
        reasoning_tokens=1,
        rate_snapshot={'billing_mode': 'search_unit', 'search_price': '0.25'},
    )
    account = UsageAccount(user_id=invocation.user_id, limit_tokens=1, used_tokens=0, reserved_tokens=100)
    outcome = await MeteringService(AsyncMock()).reconcile(
        MeterSession(invocation, account), invocation.run_id, search_units=2
    )
    assert outcome.status == 'unknown' and outcome.input_tokens is None and outcome.output_tokens is None
    assert invocation.billable_cost == Decimal('0.50') and invocation.reasoning_tokens == 1


async def test_reconciliation_adds_only_missing_usage_without_erasing_known_parts() -> None:
    user = uuid4()
    invocation = Invocation(
        version=1,
        user_id=user,
        run_id=uuid4(),
        status='unknown',
        points_enforced=True,
        reserved_tokens=100,
        operation='chat',
        input_tokens=8,
        cache_tokens=4,
        reasoning_tokens=1,
    )
    account = UsageAccount(user_id=user, limit_tokens=1, used_tokens=0, reserved_tokens=100)
    outcome = await MeteringService(AsyncMock()).reconcile(
        MeterSession(invocation, account, None), invocation.run_id, output_tokens=2
    )
    assert outcome.status == 'known' and outcome.input_tokens == 8 and outcome.output_tokens == 2
    assert invocation.cache_tokens == 4 and invocation.reasoning_tokens == 1
    assert account.reserved_tokens == 0 and account.used_tokens == 10


async def test_reconciliation_missing_invocation_is_a_conflict() -> None:
    with pytest.raises(AppError) as unavailable:
        await MeteringService(AsyncMock()).reconcile(MeterSession(None), uuid4(), input_tokens=1, output_tokens=1)
    assert unavailable.value.status_code == 409 and unavailable.value.code == 'invocation_unavailable'


@pytest.mark.parametrize('field', ['input_tokens', 'output_tokens', 'cache_tokens', 'search_units', 'reasoning_tokens'])
async def test_reconciliation_cannot_release_an_invocation_with_known_usage(field: str) -> None:
    user = uuid4()
    invocation = Invocation(
        version=1,
        user_id=user,
        run_id=uuid4(),
        status='unknown',
        points_enforced=True,
        reserved_tokens=100,
        operation='rerank',
        **{field: 1000 if field == 'input_tokens' else 1},
    )
    account = UsageAccount(user_id=user, limit_tokens=1, used_tokens=0, reserved_tokens=100)
    known = {
        name: getattr(invocation, name)
        for name in ('input_tokens', 'output_tokens', 'cache_tokens', 'search_units', 'reasoning_tokens')
    }
    with pytest.raises(AppError) as conflict:
        await MeteringService(AsyncMock()).reconcile(
            MeterSession(invocation, account), invocation.run_id, rejected_before_execution=True
        )
    assert conflict.value.status_code == 409
    assert invocation.status == 'unknown'
    assert all(getattr(invocation, name) == value for name, value in known.items())
    assert account.reserved_tokens == 100 and account.used_tokens == 0


@pytest.mark.parametrize('field', ['input_tokens', 'output_tokens', 'cache_tokens', 'search_units'])
@pytest.mark.parametrize('known', [0, 1])
async def test_reconciliation_cannot_overwrite_any_known_counter_including_zero(field: str, known: int) -> None:
    user = uuid4()
    invocation = Invocation(
        version=1,
        user_id=user,
        run_id=uuid4(),
        status='unknown',
        points_enforced=True,
        reserved_tokens=100,
        operation='rerank',
        input_tokens=8,
        cache_tokens=0,
    )
    setattr(invocation, field, known)
    account = UsageAccount(user_id=user, limit_tokens=1, used_tokens=0, reserved_tokens=100)
    values = {'input_tokens': 8, 'output_tokens': 2, 'cache_tokens': 0, 'search_units': 2}
    values[field] = known + 1
    with pytest.raises(AppError) as conflict:
        await MeteringService(AsyncMock()).reconcile(MeterSession(invocation, account), invocation.run_id, **values)
    assert conflict.value.status_code == 409 and getattr(invocation, field) == known
    assert invocation.status == 'unknown' and account.reserved_tokens == 100 and account.used_tokens == 0


async def test_reconciliation_can_repeat_known_zero_while_filling_unknown_counters() -> None:
    user = uuid4()
    invocation = Invocation(
        version=1,
        user_id=user,
        run_id=uuid4(),
        status='unknown',
        points_enforced=True,
        reserved_tokens=100,
        operation='chat',
        input_tokens=0,
        cache_tokens=0,
    )
    account = UsageAccount(user_id=user, limit_tokens=1, used_tokens=0, reserved_tokens=100)
    outcome = await MeteringService(AsyncMock()).reconcile(
        MeterSession(invocation, account, None), invocation.run_id, input_tokens=0, output_tokens=2, cache_tokens=0
    )
    assert outcome.status == 'known' and outcome.input_tokens == 0 and outcome.output_tokens == 2
    assert account.reserved_tokens == 0 and account.used_tokens == 2


async def test_repeated_known_reconciliation_cannot_report_a_conflicting_counter() -> None:
    invocation = Invocation(version=1, run_id=uuid4(), status='known', input_tokens=8, output_tokens=2)
    with pytest.raises(AppError) as conflict:
        await MeteringService(AsyncMock()).reconcile(MeterSession(invocation), invocation.run_id, input_tokens=9)
    assert conflict.value.status_code == 409 and invocation.input_tokens == 8
