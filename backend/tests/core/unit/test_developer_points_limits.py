from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import Select

from zhiheng.modules.billing.public import KeyUsageFacts
from zhiheng.modules.developer.limits import reserve_limits
from zhiheng.modules.developer.orm import DeveloperKey, KeyMinuteBucket
from zhiheng.shared.errors import AppError


class LimitSession:
    def __init__(self, *, requests: int = 0, tokens: int = 0, active: int = 0) -> None:
        self.bucket = KeyMinuteBucket(key_id=uuid4(), minute=1, requests=requests, tokens=tokens)
        self.active = active

    async def scalar(self, query: object) -> object:
        assert isinstance(query, Select)
        selection = query.column_descriptions[0]
        if selection['entity'] is KeyMinuteBucket:
            return self.bucket
        raise AssertionError(f'Unexpected quota query: {query}')


def facts(active: int = 0) -> SimpleNamespace:
    return SimpleNamespace(key_usage=AsyncMock(return_value=KeyUsageFacts(active=active, spent=Decimal(0))))


def key(**limits: object) -> DeveloperKey:
    return DeveloperKey(id=uuid4(), application_id=uuid4(), currency='USD', **limits)


async def test_key_without_money_rate_still_reserves_request_and_token_limits() -> None:
    session = LimitSession()
    minute, month, cost = await reserve_limits(session, key(rpm_limit=2, tpm_limit=100), 25, None, facts=facts())
    assert minute > 0 and len(month) == 7 and cost is None
    assert session.bucket.requests == 1 and session.bucket.tokens == 25


@pytest.mark.parametrize(
    'limits,previous',
    [({'rpm_limit': 1}, {'requests': 1}), ({'tpm_limit': 20}, {}), ({'concurrency_limit': 1}, {'active': 1})],
)
async def test_key_without_money_rate_cannot_bypass_existing_limits(limits: dict, previous: dict) -> None:
    with pytest.raises(AppError) as error:
        await reserve_limits(LimitSession(**previous), key(**limits), 25, None, facts=facts(previous.get('active', 0)))
    assert error.value.code == 'rate_limit_exceeded'


async def test_money_budget_requires_a_money_rate() -> None:
    with pytest.raises(AppError) as error:
        await reserve_limits(LimitSession(), key(monthly_budget=Decimal(1)), 25, None, facts=facts())
    assert error.value.code == 'money_rate_required'


async def test_disabled_billing_ignores_token_and_money_budgets_without_reserving_tokens() -> None:
    session = LimitSession(tokens=1000)
    _, _, cost = await reserve_limits(
        session,
        key(rpm_limit=5, tpm_limit=1, monthly_budget=Decimal(0)),
        1000000,
        None,
        facts=facts(),
        enforce_usage_limits=False,
    )
    assert cost is None
    assert session.bucket.requests == 1
    assert session.bucket.tokens == 1000


@pytest.mark.parametrize(
    'limits,previous', [({'rpm_limit': 1}, {'requests': 1}), ({'concurrency_limit': 1}, {'active': 1})]
)
async def test_disabled_billing_preserves_request_frequency_and_concurrency(limits: dict, previous: dict) -> None:
    with pytest.raises(AppError) as error:
        await reserve_limits(
            LimitSession(**previous),
            key(tpm_limit=1, monthly_budget=Decimal(0), **limits),
            1000000,
            None,
            facts=facts(previous.get('active', 0)),
            enforce_usage_limits=False,
        )
    assert error.value.code == 'rate_limit_exceeded'
