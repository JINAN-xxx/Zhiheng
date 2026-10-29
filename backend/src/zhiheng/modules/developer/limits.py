"""Durable, transaction-scoped admission; the caller holds the key row lock."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from zhiheng.modules.developer.orm import DeveloperKey, Invocation, KeyMinuteBucket
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.models.public import RateInfo


def reserved_cost(rate: RateInfo, tokens: int) -> Decimal:
    if rate.billing_mode == 'search_unit':
        return rate.search_price
    return max(rate.input_price, rate.output_price, rate.cache_price) * tokens


async def reserve_limits(
    session: AsyncSession, key: DeveloperKey, tokens: int, rate: RateInfo | None
) -> tuple[int, str, Decimal | None]:
    now = utcnow()
    minute, month = int(now.timestamp()) // 60, now.strftime('%Y-%m')
    cost = reserved_cost(rate, tokens) if rate is not None else None
    if rate is None and key.monthly_budget is not None:
        raise AppError('money_rate_required', '此密钥设有金额预算，模型必须配置同币种金额费率', 409)
    if rate is not None and rate.currency != key.currency:
        raise AppError('currency_mismatch', 'Rate and budget currencies differ', 409)
    bucket = await session.scalar(
        select(KeyMinuteBucket).where(KeyMinuteBucket.key_id == key.id, KeyMinuteBucket.minute == minute)
    )
    if bucket is None:
        bucket = KeyMinuteBucket(key_id=key.id, minute=minute, requests=0, tokens=0)
        session.add(bucket)
    active = await session.scalar(
        select(func.count())
        .select_from(Invocation)
        .join(Run, Run.id == Invocation.run_id)
        .where(Invocation.key_id == key.id, Run.status.in_(('pending', 'running', 'cancel_requested')))
    )
    # Unknown invocations retain their exact original reservation. Completed amounts replace it.
    spent = await session.scalar(
        select(func.coalesce(func.sum(func.coalesce(Invocation.billable_cost, Invocation.reserved_cost)), 0)).where(
            Invocation.key_id == key.id, Invocation.quota_month == month, Invocation.currency == key.currency
        )
    )
    if (
        (key.rpm_limit is not None and bucket.requests >= key.rpm_limit)
        or (key.tpm_limit is not None and bucket.tokens + tokens > key.tpm_limit)
        or (key.concurrency_limit is not None and (active or 0) >= key.concurrency_limit)
        or (
            key.monthly_budget is not None
            and (key.monthly_budget == 0 or (spent or Decimal(0)) + (cost or Decimal(0)) > key.monthly_budget)
        )
    ):
        raise AppError('rate_limit_exceeded', 'API key quota exceeded', 429)
    bucket.requests += 1
    bucket.tokens += tokens
    key.last_used_at = now
    return minute, month, cost


async def settle_minute(session: AsyncSession, invocation: Invocation, tokens: int) -> None:
    if invocation.key_id is None:
        return
    bucket = await session.scalar(
        select(KeyMinuteBucket).where(
            KeyMinuteBucket.key_id == invocation.key_id, KeyMinuteBucket.minute == invocation.quota_minute
        )
    )
    if bucket is None:
        raise RuntimeError('Quota reservation bucket missing')
    bucket.tokens += tokens - invocation.reserved_tokens
