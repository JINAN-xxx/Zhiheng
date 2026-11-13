"""Meter facts belong to billing; Key policy consumes a read-only public port."""

import ast
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zhiheng.modules.developer.limits import check_limits
from zhiheng.modules.developer.orm import DeveloperKey
from zhiheng.shared.errors import AppError


@pytest.mark.parametrize('name', ['UsageAccount', 'Invocation'])
def test_billing_owns_meter_storage_without_developer_forwarders(name: str) -> None:
    billing = import_module('zhiheng.modules.billing.orm')
    developer = import_module('zhiheng.modules.developer.orm')
    assert hasattr(billing, name), f'{name} must be declared by billing'
    assert getattr(billing, name).__module__ == billing.__name__
    assert not hasattr(developer, name)


@pytest.mark.parametrize(
    'name', ['UsageOutcome', 'UsageRecord', 'UsageSummary', 'ApplicationUsage', 'MeterReservation']
)
def test_billing_owns_all_usage_contracts(name: str) -> None:
    billing = import_module('zhiheng.modules.billing.contracts')
    developer = import_module('zhiheng.modules.developer.contracts')
    assert hasattr(billing, name), f'{name} must be declared by billing'
    assert getattr(billing, name).__module__ == billing.__name__
    assert not hasattr(developer, name)


def test_schema_inventory_assigns_meter_tables_to_billing_only() -> None:
    billing = import_module('zhiheng.modules.billing.schema')
    developer = import_module('zhiheng.modules.developer.schema')
    names = {'developer_accounts', 'developer_invocations'}
    table_names = [table.name for table in billing.schema_tables()]
    assert len(table_names) == len(set(table_names)), 'Billing migration inventory must not repeat tables'
    assert names <= set(table_names)
    assert not names.intersection(table.name for table in developer.schema_tables())


def test_access_policy_and_metering_do_not_import_each_others_storage() -> None:
    root = Path(__file__).parents[3] / 'src/zhiheng/modules'
    for owner, other in [('billing', 'developer'), ('developer', 'billing')]:
        for path in (root / owner).glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.ImportFrom):
                    assert node.module != f'zhiheng.modules.{other}.orm', path.name


@pytest.mark.parametrize(
    ('active', 'spent', 'limits'),
    [(1, '0', {'concurrency_limit': 1}), (0, '1.1', {'monthly_budget': Decimal('1')})],
)
async def test_key_policy_uses_public_usage_facts_for_concurrency_and_money(
    active: int,
    spent: str,
    limits: dict[str, object],
) -> None:
    # A policy session can read only its own minute bucket. Meter SQL belongs to billing.
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    facts = SimpleNamespace(key_usage=AsyncMock(return_value=SimpleNamespace(active=active, spent=Decimal(spent))))
    key = DeveloperKey(id=uuid4(), currency='USD', **limits)
    rate = SimpleNamespace(
        billing_mode='token', input_price=Decimal(0), output_price=Decimal(0), cache_price=Decimal(0), currency='USD'
    )
    with pytest.raises(AppError) as rejected:
        await check_limits(session, key, 1, rate, facts=facts)
    assert rejected.value.code == 'rate_limit_exceeded'
    assert facts.key_usage.await_args.args == (session, key.id)
    assert facts.key_usage.await_args.kwargs['currency'] == 'USD'
    assert session.scalar.await_count == 1
