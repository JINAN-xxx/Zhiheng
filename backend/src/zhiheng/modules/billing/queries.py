"""Read-only credit state, account, ledger and reservation queries."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.contracts import (
    CreditLedgerEntry,
    CreditState,
    CreditSummary,
)
from zhiheng.modules.billing.orm import (
    CreditAccount,
    CreditControl,
    CreditHold,
    CreditLedger,
)
from zhiheng.modules.billing.projections import ZERO, account_summary, reservation_info
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MAX_PAGE_SIZE = 200


class CreditQueries:
    async def state(self, session: AsyncSession) -> CreditState:
        row = await session.scalar(
            select(CreditControl).where(CreditControl.id == 1).execution_options(populate_existing=True)
        )
        return CreditState(enabled=row.enabled, version=row.version) if row else CreditState(enabled=False, version=0)

    async def summary(self, session: AsyncSession, user_id: UUID) -> CreditSummary:
        row = await session.get(CreditAccount, user_id)
        if row is None:
            return CreditSummary(
                user_id=user_id,
                enabled=True,
                version=0,
                allocated_points=ZERO,
                reclaimed_points=ZERO,
                used_points=ZERO,
                reserved_points=ZERO,
                available_points=ZERO,
            )
        return account_summary(row)

    async def summaries(self, session: AsyncSession, user_ids: list[UUID]) -> list[CreditSummary]:
        rows = await session.scalars(select(CreditAccount).where(CreditAccount.user_id.in_(user_ids)))
        known = {row.user_id: account_summary(row) for row in rows}
        return [
            known[user_id]
            if user_id in known
            else CreditSummary(
                user_id=user_id,
                enabled=True,
                version=0,
                allocated_points=ZERO,
                reclaimed_points=ZERO,
                used_points=ZERO,
                reserved_points=ZERO,
                available_points=ZERO,
            )
            for user_id in user_ids
        ]

    async def ledger(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        user_id: UUID | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        user_id = actor.id if user_id is None else user_id
        self._read_account(actor, user_id)
        self._page(page, page_size)
        query = select(CreditLedger).where(CreditLedger.user_id == user_id)
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(CreditLedger.created_at.desc(), CreditLedger.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {
            'items': [
                CreditLedgerEntry.model_validate(row, from_attributes=True).model_dump(mode='json') for row in rows
            ],
            'total': total or 0,
            'page': page,
            'page_size': page_size,
        }

    async def reservations(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        user_id: UUID | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        user_id = actor.id if user_id is None else user_id
        self._read_account(actor, user_id)
        self._page(page, page_size)
        query = select(CreditHold).where(CreditHold.user_id == user_id)
        if status is not None:
            if status not in ('reserved', 'unknown', 'settled'):
                raise AppError('invalid_status', '积分用量状态无效', 422)
            query = query.where(CreditHold.status == status)
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(CreditHold.created_at.desc(), CreditHold.run_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {
            'items': [reservation_info(row).model_dump(mode='json') for row in rows],
            'total': total or 0,
            'page': page,
            'page_size': page_size,
        }

    @staticmethod
    def _read_account(actor: Principal, user_id: UUID) -> None:
        if user_id != actor.id:
            actor.require_permission('ai:credits:read')

    @staticmethod
    def _page(page: int, page_size: int) -> None:
        if type(page) is not int or type(page_size) is not int or page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise AppError('invalid_pagination', '分页参数无效', 422)
