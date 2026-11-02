from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Select
from sqlalchemy.sql.functions import coalesce, count

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
        if isinstance(selection['expr'], count):
            return self.active
        if isinstance(selection['expr'], coalesce):
            return Decimal(0)
        raise AssertionError(f'Unexpected quota query: {query}')


def key(**limits: object) -> DeveloperKey:
    return DeveloperKey(id=uuid4(), application_id=uuid4(), currency='USD', **limits)


async def test_key_without_money_rate_still_reserves_request_and_token_limits() -> None:
    session = LimitSession()
    minute, month, cost = await reserve_limits(session, key(rpm_limit=2, tpm_limit=100), 25, None)
    assert minute > 0 and len(month) == 7 and cost is None
    assert session.bucket.requests == 1 and session.bucket.tokens == 25


@pytest.mark.parametrize(
    'limits,previous',
    [({'rpm_limit': 1}, {'requests': 1}), ({'tpm_limit': 20}, {}), ({'concurrency_limit': 1}, {'active': 1})],
)
async def test_key_without_money_rate_cannot_bypass_existing_limits(limits: dict, previous: dict) -> None:
    with pytest.raises(AppError) as error:
        await reserve_limits(LimitSession(**previous), key(**limits), 25, None)
    assert error.value.code == 'rate_limit_exceeded'


async def test_money_budget_requires_a_money_rate() -> None:
    with pytest.raises(AppError) as error:
        await reserve_limits(LimitSession(), key(monthly_budget=Decimal(1)), 25, None)
    assert error.value.code == 'money_rate_required'
