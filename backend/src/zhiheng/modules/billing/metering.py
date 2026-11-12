"""Platform Token and monetary metering, independent of Developer Key policy storage."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Literal, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from zhiheng.modules.billing.contracts import (
    ApplicationUsage,
    MeterReservation,
    UsageOutcome,
    UsageRecord,
    UsageSummary,
)
from zhiheng.modules.billing.orm import CreditLedger, Invocation, UsageAccount
from zhiheng.modules.billing.pricing import reserved_cost
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.billing.ports import KeyMeteringPolicyPort
    from zhiheng.modules.models.public import RateInfo


class MeteringService:
    """Own Token accounts, immutable monetary rate snapshots, and invocation facts."""

    def __init__(self, keys: KeyMeteringPolicyPort) -> None:
        self._keys = keys

    async def preflight(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        key_id: UUID,
        tokens: int,
        token_limit: int,
        rate: RateInfo | None,
        points_enforced: bool,
    ) -> MeterReservation:
        key = await self._keys.preflight(
            session,
            key_id,
            tokens if points_enforced else 0,
            rate if points_enforced else None,
            enforce_usage_limits=points_enforced,
        )
        return MeterReservation(points_enforced, key.key_id, key.key_limit)

    async def require_reservation(
        self,
        session: AsyncSession,
        run_id: UUID,
        user_id: UUID,
        model_id: UUID,
    ) -> MeterReservation:
        invocation = await session.scalar(
            select(Invocation).where(
                Invocation.run_id == run_id,
                Invocation.user_id == user_id,
                Invocation.model_id == model_id,
                Invocation.status == 'pending',
            )
        )
        if invocation is None:
            raise AppError('invocation_unavailable', '模型调用预留不存在或已处理', 409)
        key_limit = None
        if invocation.key_id is not None:
            key_limit = await self._keys.require_reservation(
                session,
                invocation.key_id,
                invocation.application_id,
                invocation.model_id,
            )
        return MeterReservation(bool(invocation.points_enforced), invocation.key_id, key_limit)

    async def reserve(  # noqa: PLR0913 - metering admission boundary
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        run_id: UUID,
        model_id: UUID,
        source: str,
        tokens: int,
        token_limit: int,
        key_id: UUID | None = None,
        rate: RateInfo | None = None,
        operation: str = 'chat',
        points_enforced: bool = False,
    ) -> MeterReservation:
        reserved_tokens = tokens if points_enforced else 0
        rate = rate if points_enforced else None
        key = (
            await self._keys.reserve(session, key_id, reserved_tokens, rate, enforce_usage_limits=points_enforced)
            if key_id is not None
            else None
        )
        cost = key.reserved_cost if key else reserved_cost(rate, reserved_tokens) if rate is not None else None

        await session.execute(
            insert(UsageAccount)
            .values(user_id=user_id, limit_tokens=token_limit, used_tokens=0, reserved_tokens=0)
            .on_conflict_do_nothing(index_elements=['user_id'])
        )
        account = await session.scalar(
            select(UsageAccount)
            .where(UsageAccount.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise RuntimeError('Usage account reservation invariant failed')
        account.reserved_tokens += reserved_tokens
        session.add(
            Invocation(
                run_id=run_id,
                user_id=user_id,
                model_id=model_id,
                source=source,
                reserved_tokens=reserved_tokens,
                application_id=key.application_id if key else None,
                key_id=key.key_id if key else None,
                operation=operation,
                points_enforced=points_enforced,
                rate_card_id=rate.id if rate else None,
                rate_version=rate.version if rate else None,
                rate_snapshot=rate.model_dump(
                    mode='json', include={'billing_mode', 'input_price', 'output_price', 'cache_price', 'search_price'}
                )
                if rate
                else None,
                currency=rate.currency if rate else None,
                reserved_cost=cost,
                quota_minute=key.quota_minute if key else None,
                quota_month=key.quota_month if key else None,
            )
        )
        await session.flush()
        return MeterReservation(points_enforced, key.key_id if key else None, key.key_limit if key else None)

    async def settle(
        self,
        session: AsyncSession,
        run_id: UUID,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        rejected_before_execution: bool = False,
        search_units: int | None = None,
        cache_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> UsageOutcome:
        return await self._settle(
            session,
            run_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            rejected_before_execution=rejected_before_execution,
            search_units=search_units,
            cache_tokens=cache_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    async def reconcile(
        self,
        session: AsyncSession,
        run_id: UUID,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        rejected_before_execution: bool = False,
        search_units: int | None = None,
        cache_tokens: int | None = None,
    ) -> UsageOutcome:
        return await self._settle(
            session,
            run_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            rejected_before_execution=rejected_before_execution,
            search_units=search_units,
            cache_tokens=cache_tokens,
            allow_unknown=True,
        )

    async def _settle(  # noqa: PLR0912 - reconcile partial billing units without inventing diagnostic counters
        self,
        session: AsyncSession,
        run_id: UUID,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        rejected_before_execution: bool = False,
        search_units: int | None = None,
        cache_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        allow_unknown: bool = False,
    ) -> UsageOutcome:
        invocation = await session.scalar(
            select(Invocation)
            .where(Invocation.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if invocation is None:
            if allow_unknown:
                raise AppError('invocation_unavailable', '模型调用预留不存在或已处理', 409)
            raise RuntimeError('Invocation reservation is missing')
        if allow_unknown:
            self._validate_reconciliation(
                invocation,
                {
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'search_units': search_units,
                    'cache_tokens': cache_tokens,
                    'reasoning_tokens': reasoning_tokens,
                },
                rejected_before_execution=rejected_before_execution,
            )
        if invocation.status != ('unknown' if allow_unknown else 'pending'):
            return self._outcome(invocation)
        if invocation.key_id is not None:
            await self._keys.lock(session, invocation.key_id)
        if rejected_before_execution:
            input_tokens, output_tokens, search_units = 0, 0, 0
            cache_tokens, reasoning_tokens = 0, 0
        elif allow_unknown:
            input_tokens = invocation.input_tokens if input_tokens is None else input_tokens
            output_tokens = invocation.output_tokens if output_tokens is None else output_tokens
            search_units = invocation.search_units if search_units is None else search_units
            cache_tokens = invocation.cache_tokens if cache_tokens is None else cache_tokens
            reasoning_tokens = invocation.reasoning_tokens if reasoning_tokens is None else reasoning_tokens
        if any(
            value is not None and value < 0
            for value in (
                input_tokens,
                output_tokens,
                cache_tokens,
                reasoning_tokens,
                search_units,
            )
        ) or (input_tokens is not None and (cache_tokens or 0) > input_tokens):
            raise ValueError('Invalid model usage')
        snapshot = invocation.rate_snapshot
        invocation.search_units = search_units
        invocation.cache_tokens = cache_tokens
        invocation.reasoning_tokens = reasoning_tokens
        # A provider can confirm a search charge without reporting any Tokens.
        # Preserve the missing Token counters and still enforce the real money
        # amount on the next admission against the key's currency budget.
        if snapshot is not None and snapshot['billing_mode'] == 'search_unit' and search_units is not None:
            invocation.billable_cost = Decimal(snapshot['search_price']) * search_units
        missing = (
            input_tokens is None
            or output_tokens is None
            or (
                invocation.operation == 'rerank'
                and (invocation.points_enforced or snapshot is not None)
                and search_units is None
            )
        )
        # Unknown outcomes may subsequently waive their credit hold. Take the
        # Token lock before the credit account even when counters are missing.
        account = await session.scalar(
            select(UsageAccount)
            .where(UsageAccount.user_id == invocation.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise RuntimeError('Usage account is missing')
        if missing:
            invocation.input_tokens, invocation.output_tokens = input_tokens, output_tokens
            invocation.status = 'unknown'
            search_confirmed = (
                invocation.points_enforced and invocation.operation == 'rerank' and search_units is not None
            )
            if allow_unknown and not search_confirmed:
                raise AppError('usage_required', '核对需要完整的实际用量或确认未执行', 422)
            await session.flush()
            return self._versioned_outcome(invocation)
        assert input_tokens is not None and output_tokens is not None
        if snapshot is not None:
            invocation.billable_cost = (
                Decimal(snapshot['search_price']) * (search_units or 0)
                if snapshot['billing_mode'] == 'search_unit'
                else Decimal(snapshot['input_price']) * (input_tokens - (cache_tokens or 0))
                + Decimal(snapshot['cache_price']) * (cache_tokens or 0)
                + Decimal(snapshot['output_price']) * output_tokens
            )
        if invocation.key_id is not None:
            await self._keys.settle_minute(
                session,
                key_id=invocation.key_id,
                quota_minute=invocation.quota_minute,
                reserved_tokens=invocation.reserved_tokens,
                tokens=input_tokens + output_tokens,
            )
        # The waiver already released this estimate; late real usage can still
        # be recorded without subtracting that reservation a second time.
        if not (allow_unknown and await self._failure_waived(session, run_id)):
            account.reserved_tokens -= invocation.reserved_tokens
        invocation.charged_tokens = input_tokens + output_tokens
        account.used_tokens += invocation.charged_tokens
        invocation.input_tokens, invocation.output_tokens = input_tokens, output_tokens
        invocation.status = 'known'
        await session.flush()
        return self._versioned_outcome(invocation)

    def _versioned_outcome(self, row: Invocation) -> UsageOutcome:
        row.version += 1
        return self._outcome(row)

    @staticmethod
    async def _failure_waived(session: AsyncSession, run_id: UUID) -> bool:
        return (
            await session.scalar(
                select(CreditLedger.id)
                .where(CreditLedger.run_id == run_id, CreditLedger.kind == 'failure_waive')
                .limit(1)
            )
            is not None
        )

    @staticmethod
    def _validate_reconciliation(
        invocation: Invocation,
        values: dict[str, int | None],
        *,
        rejected_before_execution: bool,
    ) -> None:
        if invocation.status == 'pending':
            raise AppError('reconciliation_unavailable', '只有未知用量可以进行核对', 409)
        known = {name: getattr(invocation, name) for name in values}
        if rejected_before_execution and any(value not in (None, 0) for value in (*known.values(), *values.values())):
            raise AppError('usage_conflict', '已报告消耗的调用不能确认为未执行', 409)
        if any(
            value is not None and known[name] is not None and value != known[name] for name, value in values.items()
        ):
            raise AppError('usage_conflict', '已知用量不可覆盖，核对仅能补充未知用量', 409)

    @staticmethod
    def record(row: Invocation) -> UsageRecord:
        return UsageRecord.model_validate({name: getattr(row, name) for name in UsageRecord.model_fields})

    async def summary(
        self,
        session: AsyncSession,
        user_id: UUID,
        *,
        token_limit: int,
        page: int = 1,
        page_size: int = 100,
        source: str | None = None,
        status: str | None = None,
    ) -> UsageSummary:
        account = await session.get(UsageAccount, user_id)
        query = select(Invocation).where(Invocation.user_id == user_id)
        if source:
            query = query.where(Invocation.source == source)
        if status:
            query = query.where(Invocation.status == status)
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = await session.scalars(
            query.order_by(Invocation.created_at.desc(), Invocation.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return UsageSummary(
            limit_tokens=account.limit_tokens if account else token_limit,
            quota_version=account.quota_version if account else 0,
            used_tokens=account.used_tokens if account else 0,
            reserved_tokens=account.reserved_tokens if account else 0,
            total=total,
            page=page,
            page_size=page_size,
            records=[self.record(row) for row in rows],
        )

    async def application_usage(
        self,
        session: AsyncSession,
        application_id: UUID,
        page: int,
        page_size: int,
        operation: str | None,
        status: str | None,
    ) -> dict[str, object]:
        query = (
            select(Invocation, Run)
            .join(Run, Run.id == Invocation.run_id)
            .where(Invocation.application_id == application_id)
        )
        if operation:
            query = query.where(Invocation.operation == operation)
        if status:
            query = query.where(Invocation.status == status)
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.execute(
            query.order_by(Invocation.created_at.desc(), Invocation.id).offset((page - 1) * page_size).limit(page_size)
        )
        items = []
        for invocation, run in rows:
            info = ApplicationUsage.model_validate(invocation, from_attributes=True)
            items.append(
                {
                    **info.model_dump(mode='json'),
                    'run_status': run.status,
                    'error': {'code': run.error_code, 'message': run.error_message} if run.error_code else None,
                }
            )
        return {'items': items, 'total': total or 0, 'page': page, 'page_size': page_size}

    @staticmethod
    def _outcome(invocation: Invocation) -> UsageOutcome:
        return UsageOutcome(
            input_tokens=invocation.input_tokens,
            output_tokens=invocation.output_tokens,
            status=cast("Literal['pending', 'known', 'unknown']", invocation.status),
            points_enforced=bool(invocation.points_enforced),
        )
