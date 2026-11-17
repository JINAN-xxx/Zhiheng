"""Read the original billed account, independently of execution-content authority."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.contracts import CreditLedgerEntry
from zhiheng.modules.billing.orm import CreditHold, CreditLedger, Invocation
from zhiheng.modules.billing.projections import reservation_info
from zhiheng.modules.billing.trace_contracts import BillingTrace, InvocationDetail, InvocationReference
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

LEDGER_PAGE_SIZE = 20


class BillingTraceQueries:
    async def detail(
        self, session: AsyncSession, actor: Principal, run_id: UUID, *, ledger_page: int = 1
    ) -> BillingTrace:
        if type(ledger_page) is not int or ledger_page < 1:
            raise AppError('invalid_pagination', '分页参数无效', 422)
        query = select(Invocation).where(Invocation.run_id == run_id)
        if not actor.has_any_permission('ai:credits:read'):
            query = query.where(Invocation.user_id == actor.id)
        invocation = await session.scalar(query)
        if invocation is None:
            # Run initiation alone grants no access to another billed account.
            raise AppError('invocation_not_found', '调用记录不存在', 404)
        hold = await session.scalar(
            select(CreditHold).where(CreditHold.run_id == run_id, CreditHold.user_id == invocation.user_id)
        )
        ledger_query = select(CreditLedger).where(
            CreditLedger.run_id == run_id, CreditLedger.user_id == invocation.user_id
        )
        total = await session.scalar(select(func.count()).select_from(ledger_query.subquery()))
        ledger = await session.scalars(
            ledger_query.order_by(CreditLedger.created_at, CreditLedger.id)
            .offset((ledger_page - 1) * LEDGER_PAGE_SIZE)
            .limit(LEDGER_PAGE_SIZE)
        )
        return BillingTrace(
            invocation=InvocationDetail.model_validate(invocation, from_attributes=True),
            reservation=reservation_info(hold) if hold is not None else None,
            ledger=[CreditLedgerEntry.model_validate(row, from_attributes=True) for row in ledger],
            ledger_total=total or 0,
            ledger_page=ledger_page,
            ledger_page_size=LEDGER_PAGE_SIZE,
        )

    async def references(
        self, session: AsyncSession, actor: Principal, user_id: UUID, run_ids: list[UUID]
    ) -> list[InvocationReference]:
        if not run_ids:
            return []
        if user_id != actor.id and not actor.has_any_permission('ai:credits:read'):
            return []
        rows = await session.scalars(
            select(Invocation)
            .where(Invocation.run_id.in_(run_ids), Invocation.user_id == user_id)
            .order_by(Invocation.created_at, Invocation.id)
        )
        return [InvocationReference.model_validate(row, from_attributes=True) for row in rows]
