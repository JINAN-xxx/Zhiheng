"""Append-only credit price versions; callers own authorization context and commit."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.commands import CreditCommandJournal
from zhiheng.modules.billing.contracts import CreditPrices, CreditRateCreate, CreditRateInfo
from zhiheng.modules.billing.gate import lock_credit
from zhiheng.modules.billing.orm import CreditRate
from zhiheng.modules.billing.pricing import exact_points
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

PRICES = {'billing_mode', 'is_free', 'input_points', 'output_points', 'cache_points', 'search_points'}


class CreditRates:
    def __init__(self, commands: CreditCommandJournal) -> None:
        self.commands = commands

    async def list(self, session: AsyncSession, actor: Principal, model_id: UUID) -> list[CreditRateInfo]:
        actor.require_permission('ai:rates:read')
        rows = await session.scalars(
            select(CreditRate).where(CreditRate.model_id == model_id).order_by(CreditRate.version.desc())
        )
        return [CreditRateInfo.model_validate(row, from_attributes=True) for row in rows]

    async def create(
        self,
        session: AsyncSession,
        actor: Principal,
        model_id: UUID,
        data: CreditRateCreate,
        *,
        model_type: str,
    ) -> CreditRateInfo:
        actor.require_permission('ai:rates:create')
        if model_type not in ('chat', 'embedding', 'rerank') or data.billing_mode != (
            'search_unit' if model_type == 'rerank' else 'token'
        ):
            raise AppError('invalid_rate', '积分费率计量方式与模型类型不匹配', 422)
        prices = CreditPrices.model_validate(data.model_dump(include=PRICES))
        price_values = prices.model_dump()
        for name in ('input_points', 'output_points', 'cache_points', 'search_points'):
            price_values[name] = exact_points(price_values[name])
        previous = await self.commands.replay(session, actor, 'rate', str(model_id), data, CreditRateInfo)
        if previous is not None:
            return previous
        await lock_credit(session, f'rate:{model_id}')
        version = await session.scalar(select(func.max(CreditRate.version)).where(CreditRate.model_id == model_id))
        row = CreditRate(model_id=model_id, version=(version or 0) + 1, **price_values)
        session.add(row)
        await session.flush()
        result = CreditRateInfo.model_validate(row, from_attributes=True)
        await self.commands.record(session, actor, 'rate', str(model_id), data, result)
        return result

    async def quote(self, session: AsyncSession, model_id: UUID) -> CreditRateInfo | None:
        row = await session.scalar(
            select(CreditRate).where(CreditRate.model_id == model_id).order_by(CreditRate.version.desc()).limit(1)
        )
        return CreditRateInfo.model_validate(row, from_attributes=True) if row else None
