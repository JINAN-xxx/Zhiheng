from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.contracts import UsageOutcome, UsageRecord, UsageSummary
from zhiheng.modules.developer.orm import Invocation, UsageAccount
from zhiheng.shared.errors import AppError


class UsageService:
    async def require_reservation(self, session: AsyncSession, run_id: UUID, user_id: UUID, model_id: UUID) -> None:
        invocation = await session.scalar(
            select(Invocation.id).where(
                Invocation.run_id == run_id,
                Invocation.user_id == user_id,
                Invocation.model_id == model_id,
                Invocation.status == 'pending',
            )
        )
        if invocation is None:
            raise AppError('invocation_unavailable', '模型调用预留不存在或已处理', 409)

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
    ) -> None:
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
            Invocation(run_id=run_id, user_id=user_id, model_id=model_id, source=source, reserved_tokens=tokens)
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
    ) -> UsageOutcome:
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run_id).with_for_update())
        if invocation is None:
            raise RuntimeError('Invocation reservation is missing')
        if invocation.status != 'pending':
            return self._outcome(invocation)
        if rejected_before_execution:
            input_tokens, output_tokens = 0, 0
        if any(value is not None and value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError('Token usage cannot be negative')
        if input_tokens is None or output_tokens is None:
            invocation.input_tokens, invocation.output_tokens = input_tokens, output_tokens
            invocation.status = 'unknown'
            return self._outcome(invocation)
        account = await session.scalar(
            select(UsageAccount).where(UsageAccount.user_id == invocation.user_id).with_for_update()
        )
        if account is None:
            raise RuntimeError('Usage account is missing')
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

    @staticmethod
    def _outcome(invocation: Invocation) -> UsageOutcome:
        return UsageOutcome(
            input_tokens=invocation.input_tokens,
            output_tokens=invocation.output_tokens,
            status=cast("Literal['pending', 'known', 'unknown']", invocation.status),
        )
