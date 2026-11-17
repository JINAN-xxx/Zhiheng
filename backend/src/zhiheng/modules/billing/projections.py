"""Exact account/hold projections shared by read queries and monetary writes."""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import TYPE_CHECKING

from zhiheng.modules.billing.contracts import CreditReservation, CreditSummary
from zhiheng.modules.billing.pricing import POINT_QUANTUM

if TYPE_CHECKING:
    from zhiheng.modules.billing.orm import CreditAccount, CreditHold

ZERO = Decimal('0.000000000000000000')
USAGE_COUNTERS = ('input_tokens', 'output_tokens', 'cache_tokens', 'search_units', 'reasoning_tokens')


def sum_points(*values: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        return sum(values, ZERO).quantize(POINT_QUANTUM)


def available_points(account: CreditAccount) -> Decimal:
    return sum_points(
        account.allocated_points,
        account.reclaimed_points.copy_negate(),
        account.used_points.copy_negate(),
        account.reserved_points.copy_negate(),
    )


def account_summary(row: CreditAccount) -> CreditSummary:
    return CreditSummary(
        user_id=row.user_id,
        enabled=row.enabled,
        version=row.version,
        allocated_points=row.allocated_points,
        reclaimed_points=row.reclaimed_points,
        used_points=row.used_points,
        reserved_points=row.reserved_points,
        available_points=available_points(row),
    )


def reservation_info(row: CreditHold) -> CreditReservation:
    value = CreditReservation.model_validate(row, from_attributes=True)
    return CreditReservation.model_validate(
        {
            **value.model_dump(),
            'billing_mode': row.rate_snapshot['billing_mode'],
            # Ordinary settlement always has confirmed billing counters;
            # an empty failed-chat waiver deliberately preserves all nulls.
            'failure_waived': row.source == 'chat'
            and row.status == 'settled'
            and row.charged_points == ZERO
            and all(getattr(row, counter) is None for counter in USAGE_COUNTERS),
        }
    )
