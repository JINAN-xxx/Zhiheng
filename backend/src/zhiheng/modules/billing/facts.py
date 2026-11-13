"""Billing-owned projections for access policy; no ledger mutations are exposed."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from zhiheng.modules.billing.contracts import InvocationIdentity, KeyUsageFacts
from zhiheng.modules.billing.orm import Invocation
from zhiheng.runtime.orm import Run

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class UsageFactsService:
    async def key_usage(
        self,
        session: AsyncSession,
        key_id: UUID,
        *,
        month: str,
        currency: str,
    ) -> KeyUsageFacts:
        active = await session.scalar(
            select(func.count())
            .select_from(Invocation)
            .join(Run, Run.id == Invocation.run_id)
            .where(Invocation.key_id == key_id, Run.status.in_(('pending', 'running', 'cancel_requested')))
        )
        # Unknown usage keeps the original money reservation; confirmed costs replace it.
        spent = await session.scalar(
            select(func.coalesce(func.sum(func.coalesce(Invocation.billable_cost, Invocation.reserved_cost)), 0)).where(
                Invocation.key_id == key_id, Invocation.quota_month == month, Invocation.currency == currency
            )
        )
        return KeyUsageFacts(active=active or 0, spent=spent or Decimal(0))

    async def invocation_identity(self, session: AsyncSession, run_id: UUID) -> InvocationIdentity | None:
        row = await session.scalar(select(Invocation).where(Invocation.run_id == run_id))
        if row is None:
            return None
        return InvocationIdentity(row.application_id, row.key_id, row.user_id, row.model_id)
