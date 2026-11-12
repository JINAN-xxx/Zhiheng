"""Durable, transaction-scoped admission; the caller holds the key row lock."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select

from zhiheng.modules.billing.public import reserved_cost
from zhiheng.modules.developer.orm import DeveloperKey, KeyMinuteBucket
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.billing.public import UsageFactsPort
    from zhiheng.modules.models.public import RateInfo


async def reserve_limits(
    session: AsyncSession,
    key: DeveloperKey,
    tokens: int,
    rate: RateInfo | None,
    *,
    facts: UsageFactsPort,
    enforce_usage_limits: bool = True,
) -> tuple[int, str, Decimal | None]:
    tokens = tokens if enforce_usage_limits else 0
    minute, month, cost = await check_limits(
        session, key, tokens, rate, facts=facts, enforce_usage_limits=enforce_usage_limits
    )
    bucket = await session.scalar(
        select(KeyMinuteBucket).where(KeyMinuteBucket.key_id == key.id, KeyMinuteBucket.minute == minute)
    )
    if bucket is None:
        bucket = KeyMinuteBucket(key_id=key.id, minute=minute, requests=0, tokens=0)
        session.add(bucket)
    bucket.requests += 1
    bucket.tokens += tokens
    key.last_used_at = utcnow()
    return minute, month, cost


async def check_limits(
    session: AsyncSession,
    key: DeveloperKey,
    tokens: int,
    rate: RateInfo | None,
    *,
    facts: UsageFactsPort,
    enforce_usage_limits: bool = True,
) -> tuple[int, str, Decimal | None]:
    """The same native quota rules without consuming an invocation or creating a bucket."""
    now = utcnow()
    minute, month = int(now.timestamp()) // 60, now.strftime('%Y-%m')
    cost = reserved_cost(rate, tokens) if enforce_usage_limits and rate is not None else None
    if enforce_usage_limits and rate is None and key.monthly_budget is not None:
        raise AppError('money_rate_required', '此密钥设有金额预算，模型必须配置同币种金额费率', 409)
    if enforce_usage_limits and rate is not None and rate.currency != key.currency:
        raise AppError('currency_mismatch', 'Rate and budget currencies differ', 409)
    bucket = await session.scalar(
        select(KeyMinuteBucket).where(KeyMinuteBucket.key_id == key.id, KeyMinuteBucket.minute == minute)
    )
    usage = await facts.key_usage(session, key.id, month=month, currency=key.currency)
    if (
        (key.rpm_limit is not None and (bucket.requests if bucket else 0) >= key.rpm_limit)
        or (
            enforce_usage_limits
            and key.tpm_limit is not None
            and (bucket.tokens if bucket else 0) + tokens > key.tpm_limit
        )
        or (key.concurrency_limit is not None and usage.active >= key.concurrency_limit)
        or (
            enforce_usage_limits
            and key.monthly_budget is not None
            and (key.monthly_budget == 0 or usage.spent + (cost or Decimal(0)) > key.monthly_budget)
        )
    ):
        raise AppError('rate_limit_exceeded', 'API key quota exceeded', 429)
    return minute, month, cost


async def settle_minute(
    session: AsyncSession,
    *,
    key_id: UUID,
    quota_minute: int | None,
    reserved_tokens: int,
    tokens: int,
) -> None:
    bucket = await session.scalar(
        select(KeyMinuteBucket).where(KeyMinuteBucket.key_id == key_id, KeyMinuteBucket.minute == quota_minute)
    )
    if bucket is None:
        raise RuntimeError('Quota reservation bucket missing')
    bucket.tokens += tokens - reserved_tokens
