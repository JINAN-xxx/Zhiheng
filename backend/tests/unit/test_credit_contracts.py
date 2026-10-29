from decimal import Decimal
from importlib import import_module
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError


def contracts() -> ModuleType:
    path = Path(__file__).parents[2] / 'src/zhiheng/modules/billing/contracts.py'
    assert path.is_file(), 'Platform credit contracts are not implemented'
    return import_module('zhiheng.modules.billing.contracts')


@pytest.mark.parametrize('value', [0.1, 1, True, 'NaN', 'Infinity', '-1', '0', '0.0000000000000000001'])
def test_adjustment_requires_positive_exact_decimal_string(value: object) -> None:
    with pytest.raises(ValidationError):
        contracts().CreditAdjustment(kind='grant', points=value, reason='manual grant', client_request_id='grant-1')


def test_adjustment_accepts_exact_points_and_serializes_as_string() -> None:
    data = contracts().CreditAdjustment(
        kind='grant', points='100.125', reason='manual grant', client_request_id='grant-1'
    )
    assert data.points == Decimal('100.125')
    assert data.model_dump(mode='json')['points'] == '100.125'


def test_all_zero_rate_requires_explicit_free_flag() -> None:
    with pytest.raises(ValidationError):
        contracts().CreditRateCreate(billing_mode='token', reason='initial', client_request_id='rate-1')
    free = contracts().CreditRateCreate(billing_mode='token', is_free=True, reason='free', client_request_id='rate-2')
    assert free.is_free


def test_free_rate_cannot_hide_nonzero_price() -> None:
    with pytest.raises(ValidationError):
        contracts().CreditRateCreate(
            billing_mode='token', is_free=True, input_points='1', reason='free', client_request_id='rate-1'
        )


def test_price_computation_uses_per_million_and_cache_replaces_input() -> None:
    c = contracts()
    pricing = import_module('zhiheng.modules.billing.pricing')
    rate = c.CreditRateCreate(
        billing_mode='token',
        input_points='100',
        output_points='500',
        cache_points='25',
        reason='initial',
        client_request_id='rate-1',
    )
    assert pricing.points_for_usage(rate, input_tokens=1000, output_tokens=1000) == Decimal('0.6')
    assert pricing.points_for_usage(rate, input_tokens=1000, output_tokens=200) == Decimal('0.2')
    assert pricing.points_for_usage(rate, input_tokens=1000, output_tokens=0, cache_tokens=400) == Decimal('0.07')


def test_cache_over_total_input_is_rejected() -> None:
    c = contracts()
    pricing = import_module('zhiheng.modules.billing.pricing')
    rate = c.CreditRateCreate(billing_mode='token', input_points='100', reason='initial', client_request_id='rate-1')
    with pytest.raises(ValueError):
        pricing.points_for_usage(rate, input_tokens=1, output_tokens=0, cache_tokens=2)


def test_account_arithmetic_keeps_all_38_digits() -> None:
    service = import_module('zhiheng.modules.billing.service')
    account = SimpleNamespace(
        allocated_points=Decimal('99999999999999999999.123456789123456789'),
        reclaimed_points=Decimal('99999999999999999999.123456789123456788'),
        used_points=Decimal(0),
        reserved_points=Decimal(0),
    )
    assert service._available(account) == Decimal('0.000000000000000001')


def test_points_json_always_uses_plain_decimal_strings() -> None:
    c = contracts()
    result = c.CreditSummary(
        user_id=uuid4(),
        enabled=True,
        version=0,
        allocated_points=Decimal('0E-18'),
        reclaimed_points=Decimal('1E-18'),
        used_points=Decimal('-0E-18'),
        reserved_points=Decimal('0E-18'),
        available_points=Decimal('0E-18'),
    ).model_dump(mode='json')
    assert result['allocated_points'] == '0.000000000000000000'
    assert result['reclaimed_points'] == '0.000000000000000001'
    assert result['used_points'] == '0.000000000000000000'


@pytest.mark.parametrize(
    'counts',
    [
        {'input_tokens': 10, 'cache_tokens': 11},
        {'input_tokens': 2_147_483_648},
        {'search_units': 2_147_483_648},
    ],
)
def test_reconcile_rejects_impossible_or_unstorable_usage(counts: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        contracts().CreditReconcile(expected_version=1, reason='confirmed', client_request_id='confirm', **counts)
