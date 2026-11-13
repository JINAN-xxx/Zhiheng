"""Credit arithmetic: per-million token prices and per-search-unit prices."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zhiheng.modules.billing.contracts import CreditPrices
    from zhiheng.modules.models.public import RateInfo


POINT_QUANTUM = Decimal('0.000000000000000001')
MAX_POINTS = Decimal('100000000000000000000')
MILLION = Decimal(1_000_000)


def exact_points(value: Decimal | str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, (Decimal, str)):
        raise ValueError('Exact decimal points are required')
    try:
        result = Decimal(value)
        if not result.is_finite() or result < 0 or result >= MAX_POINTS or (positive and result == 0):
            raise ValueError('Points are outside the allowed range')
        with localcontext() as context:
            context.prec = 80
            normalized = result.quantize(POINT_QUANTUM)
        if normalized != result:
            raise ValueError('Points may have at most 18 decimal places')
        return normalized
    except InvalidOperation as exc:
        raise ValueError('Invalid decimal points') from exc


def points_for_usage(
    rate: CreditPrices,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_tokens: int = 0,
    search_units: int = 0,
) -> Decimal:
    for value in (input_tokens, output_tokens, cache_tokens, search_units):
        if type(value) is not int or value < 0:
            raise ValueError('Usage must be nonnegative integers')
    if cache_tokens > input_tokens:
        raise ValueError('Cached tokens cannot exceed total input tokens')
    with localcontext() as context:
        context.prec = 80
        charge = (
            rate.search_points * search_units
            if rate.billing_mode == 'search_unit'
            else (
                rate.input_points * (input_tokens - cache_tokens)
                + rate.cache_points * cache_tokens
                + rate.output_points * output_tokens
            )
            / MILLION
        )
        return charge.quantize(POINT_QUANTUM)


def reserved_cost(rate: RateInfo, tokens: int) -> Decimal:
    if rate.billing_mode == 'search_unit':
        return rate.search_price
    return max(rate.input_price, rate.output_price, rate.cache_price) * tokens
