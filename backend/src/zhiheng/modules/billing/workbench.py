"""Aggregate original invocation facts in one bounded Shanghai-time reporting window."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.orm import CreditControl, CreditHold, Invocation
from zhiheng.modules.overview.public import DailyCalls, WorkbenchCredits, WorkbenchMetrics
from zhiheng.runtime.orm import Run

REPORT_ZONE = ZoneInfo('Asia/Shanghai')


class WorkbenchBilling:
    async def metrics(self, session: AsyncSession, owner_id: UUID | None, now: datetime, days: int) -> WorkbenchMetrics:
        today = now.astimezone(REPORT_ZONE).date()
        start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time(), REPORT_ZONE)
        since = min(start, datetime.combine(today - timedelta(days=1), datetime.min.time(), REPORT_ZONE))
        day = func.date(func.timezone('Asia/Shanghai', Invocation.created_at))
        query = (
            select(
                day.label('day'),
                func.count().label('calls'),
                func.count().filter(Run.status == 'completed').label('successful'),
                func.count().filter(Run.status.in_(('failed', 'interrupted'))).label('failed'),
                func.count().filter(Run.status == 'cancelled').label('cancelled'),
                func.coalesce(
                    func.sum(func.coalesce(Invocation.input_tokens, 0) + func.coalesce(Invocation.output_tokens, 0)), 0
                ).label('tokens'),
                func.count()
                .filter(or_(Invocation.input_tokens.is_(None), Invocation.output_tokens.is_(None)))
                .label('unconfirmed'),
            )
            .join(Run, Run.id == Invocation.run_id)
            .where(Invocation.created_at >= since, Invocation.created_at <= now, Run.provider_started.is_(True))
        )
        if owner_id is not None:
            query = query.where(Invocation.user_id == owner_id)
        rows = {row.day: row for row in (await session.execute(query.group_by(day))).all()}
        current = rows.get(today)
        yesterday = rows.get(today - timedelta(days=1))
        period = [value for day_value, value in rows.items() if day_value >= start.date()]
        return WorkbenchMetrics(
            today_calls=current.calls if current else 0,
            yesterday_calls=yesterday.calls if yesterday else 0,
            successful_calls=sum(row.successful for row in period),
            failed_calls=sum(row.failed for row in period),
            cancelled_calls=sum(row.cancelled for row in period),
            today_known_tokens=int(current.tokens) if current else 0,
            today_unconfirmed_calls=current.unconfirmed if current else 0,
            trend=[
                DailyCalls(
                    day=start.date() + timedelta(days=index),
                    calls=rows[start.date() + timedelta(days=index)].calls
                    if start.date() + timedelta(days=index) in rows
                    else 0,
                )
                for index in range(days)
            ],
        )

    async def credits(self, session: AsyncSession, owner_id: UUID | None, now: datetime) -> WorkbenchCredits:
        start = now.astimezone(REPORT_ZONE).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        query = select(
            func.coalesce(func.sum(CreditHold.charged_points), 0), func.count().filter(CreditHold.status == 'unknown')
        ).where(CreditHold.created_at >= start, CreditHold.created_at <= now)
        if owner_id is not None:
            query = query.where(CreditHold.user_id == owner_id)
        points, unknown = (await session.execute(query)).one()
        enabled = await session.scalar(select(CreditControl.enabled).where(CreditControl.id == 1))
        return WorkbenchCredits(
            enabled=bool(enabled), monthly_points=format(Decimal(points), 'f'), unconfirmed_calls=unknown
        )

    async def recent_failures(self, session: AsyncSession, owner_id: UUID, now: datetime) -> int:
        return (
            await session.scalar(
                select(func.count())
                .select_from(Invocation)
                .join(Run, Run.id == Invocation.run_id)
                .where(
                    Invocation.user_id == owner_id,
                    Invocation.created_at >= now - timedelta(days=1),
                    Invocation.created_at <= now,
                    Run.status.in_(('failed', 'interrupted')),
                )
            )
            or 0
        )

    async def recent_models(self, session: AsyncSession, owner_id: UUID) -> list[tuple[UUID, datetime]]:
        used = func.max(Invocation.created_at)
        rows = await session.execute(
            select(Invocation.model_id, used)
            .join(Run, Run.id == Invocation.run_id)
            .where(Invocation.user_id == owner_id, Run.provider_started.is_(True))
            .group_by(Invocation.model_id)
            .order_by(used.desc(), Invocation.model_id)
            .limit(12)
        )
        return [(identifier, at) for identifier, at in rows]
