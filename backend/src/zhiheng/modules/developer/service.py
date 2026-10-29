from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Literal, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from zhiheng.modules.developer.contracts import (
    ApplicationUsage,
    LegacyReservation,
    UsageOutcome,
    UsageRecord,
    UsageSummary,
)
from zhiheng.modules.developer.keys import KeyService
from zhiheng.modules.developer.limits import reserve_limits, reserved_cost, settle_minute
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey, Invocation, UsageAccount
from zhiheng.modules.identity.public import IdentityService
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.models.public import RateInfo


class UsageService:
    async def require_reservation(
        self,
        session: AsyncSession,
        run_id: UUID,
        user_id: UUID,
        model_id: UUID,
    ) -> LegacyReservation:
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
            key = await session.get(DeveloperKey, invocation.key_id)
            app = await session.get(DeveloperApplication, invocation.application_id)
            if (
                key is None
                or not KeyService.usable(key)
                or app is None
                or app.status != 'active'
                or app.deleted_at is not None
                or str(invocation.model_id) not in key.model_ids
                or not await IdentityService().is_active(session, app.owner_id)
            ):
                raise AppError('invocation_unavailable', '调用授权已失效', 401)
            key_limit = key.monthly_points_limit
        return LegacyReservation(bool(invocation.points_enforced), invocation.key_id, key_limit)

    async def reserve(  # noqa: PLR0913 - historical admission boundary
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
        enforce_token_limit: bool = True,
    ) -> LegacyReservation:
        quota_minute = None
        quota_month = None
        key = (
            await session.scalar(
                select(DeveloperKey)
                .where(DeveloperKey.id == key_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if key_id is not None
            else None
        )
        if key_id is not None and key is None:
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        cost = reserved_cost(rate, tokens) if rate is not None else None
        if key is not None:
            quota_minute, quota_month, cost = await reserve_limits(session, key, tokens, rate)

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
        if enforce_token_limit and tokens > account.limit_tokens - account.used_tokens - account.reserved_tokens:
            raise AppError('quota_exceeded', '可用 token 配额不足', 429)
        account.reserved_tokens += tokens
        session.add(
            Invocation(
                run_id=run_id,
                user_id=user_id,
                model_id=model_id,
                source=source,
                reserved_tokens=tokens,
                application_id=key.application_id if key else None,
                key_id=key.id if key else None,
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
                quota_minute=quota_minute,
                quota_month=quota_month,
            )
        )
        await session.flush()
        return LegacyReservation(points_enforced, key.id if key else None, key.monthly_points_limit if key else None)

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
            await session.scalar(select(DeveloperKey).where(DeveloperKey.id == invocation.key_id).with_for_update())
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
            input_tokens is None or output_tokens is None or (invocation.operation == 'rerank' and search_units is None)
        )
        if missing:
            invocation.input_tokens, invocation.output_tokens = input_tokens, output_tokens
            invocation.status = 'unknown'
            search_confirmed = (
                invocation.points_enforced and invocation.operation == 'rerank' and search_units is not None
            )
            if allow_unknown and not search_confirmed:
                raise AppError('usage_required', '核对需要完整的实际用量或确认未执行', 422)
            await session.flush()
            return self._outcome(invocation)
        account = await session.scalar(
            select(UsageAccount)
            .where(UsageAccount.user_id == invocation.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise RuntimeError('Usage account is missing')
        assert input_tokens is not None and output_tokens is not None
        if snapshot is not None:
            invocation.billable_cost = (
                Decimal(snapshot['search_price']) * (search_units or 0)
                if snapshot['billing_mode'] == 'search_unit'
                else Decimal(snapshot['input_price']) * (input_tokens - (cache_tokens or 0))
                + Decimal(snapshot['cache_price']) * (cache_tokens or 0)
                + Decimal(snapshot['output_price']) * output_tokens
            )
        await settle_minute(session, invocation, input_tokens + output_tokens)
        account.reserved_tokens -= invocation.reserved_tokens
        invocation.charged_tokens = input_tokens + output_tokens
        account.used_tokens += invocation.charged_tokens
        invocation.input_tokens = input_tokens
        invocation.output_tokens = output_tokens
        invocation.status = 'known'
        await session.flush()
        return self._outcome(invocation)

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

    async def summary(self, session: AsyncSession, user_id: UUID, *, token_limit: int) -> UsageSummary:
        account = await session.get(UsageAccount, user_id)
        rows = await session.scalars(
            select(Invocation).where(Invocation.user_id == user_id).order_by(Invocation.created_at.desc()).limit(100)
        )
        records = [
            UsageRecord(
                id=row.id,
                run_id=row.run_id,
                model_id=row.model_id,
                source=row.source,
                status=row.status,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                charged_tokens=row.charged_tokens,
                created_at=row.created_at,
            )
            for row in rows
        ]
        return UsageSummary(
            limit_tokens=account.limit_tokens if account is not None else token_limit,
            used_tokens=account.used_tokens if account is not None else 0,
            reserved_tokens=account.reserved_tokens if account is not None else 0,
            records=records,
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
