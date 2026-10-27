from decimal import Decimal
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.contracts import ApplicationUsage, UsageOutcome, UsageRecord, UsageSummary
from zhiheng.modules.developer.keys import KeyService
from zhiheng.modules.developer.limits import reserve_limits, reserved_cost, settle_minute
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey, Invocation, UsageAccount
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.models.public import RateInfo
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError


class UsageService:
    async def require_reservation(self, session: AsyncSession, run_id: UUID, user_id: UUID, model_id: UUID) -> None:
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

    async def reserve(
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
    ) -> None:
        quota_minute = None
        quota_month = None
        key = await session.get(DeveloperKey, key_id) if key_id is not None else None
        cost = reserved_cost(rate, tokens) if rate is not None else None
        if key is not None and rate is not None:
            quota_minute, quota_month, cost = await reserve_limits(session, key, tokens, rate)

        await session.execute(
            insert(UsageAccount)
            .values(user_id=user_id, limit_tokens=token_limit, used_tokens=0, reserved_tokens=0)
            .on_conflict_do_nothing(index_elements=['user_id'])
        )
        account = await session.scalar(select(UsageAccount).where(UsageAccount.user_id == user_id).with_for_update())
        if account is None:
            raise RuntimeError('Usage account reservation invariant failed')
        if tokens > account.limit_tokens - account.used_tokens - account.reserved_tokens:
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
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run_id).with_for_update())
        if invocation is None:
            raise RuntimeError('Invocation reservation is missing')
        if invocation.status != 'pending':
            return self._outcome(invocation)
        if invocation.key_id is not None:
            await session.scalar(select(DeveloperKey).where(DeveloperKey.id == invocation.key_id).with_for_update())
        if rejected_before_execution:
            input_tokens, output_tokens, search_units = 0, 0, 0
        if any(value is not None and value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError('Token usage cannot be negative')
        snapshot = invocation.rate_snapshot
        invocation.search_units = search_units
        invocation.cache_tokens = cache_tokens
        invocation.reasoning_tokens = reasoning_tokens
        missing = (
            input_tokens is None or output_tokens is None or (invocation.operation == 'rerank' and search_units is None)
        )
        if missing:
            invocation.input_tokens, invocation.output_tokens = input_tokens, output_tokens
            invocation.status = 'unknown'
            return self._outcome(invocation)
        account = await session.scalar(
            select(UsageAccount).where(UsageAccount.user_id == invocation.user_id).with_for_update()
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
        )
