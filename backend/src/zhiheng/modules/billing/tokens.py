"""Versioned Token quota and unknown-usage commands with durable idempotency."""

import hashlib
import json
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.contracts import TokenQuotaPatch, TokenReconcile, UsageRecord, UsageSummary
from zhiheng.modules.billing.metering import MeteringService
from zhiheng.modules.billing.orm import Invocation, TokenCommand, UsageAccount
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

Result = TypeVar('Result', bound=BaseModel)


class TokenManagementService:
    def __init__(self, meter: MeteringService) -> None:
        self.meter = meter

    async def command(
        self,
        session: AsyncSession,
        actor_id: UUID,
        operation: str,
        target: UUID,
        body: TokenQuotaPatch | TokenReconcile,
    ) -> tuple[str, TokenCommand | None]:
        key = f'token-command:{actor_id}:{operation}:{body.client_request_id}'
        await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key': key})
        value = hashlib.sha256(
            json.dumps([str(target), body.model_dump(mode='json')], sort_keys=True).encode()
        ).hexdigest()
        row = await session.get(TokenCommand, (actor_id, operation, body.client_request_id))
        if row and row.fingerprint != value:
            raise AppError('idempotency_conflict', '原请求标识不能用于不同操作内容', 409)
        return value, row

    @staticmethod
    async def remember(
        session: AsyncSession,
        actor_id: UUID,
        operation: str,
        body: TokenQuotaPatch | TokenReconcile,
        value: str,
        result: Result,
    ) -> Result:
        session.add(
            TokenCommand(
                actor_id=actor_id,
                operation=operation,
                request_id=body.client_request_id,
                fingerprint=value,
                reason=body.reason,
                result=result.model_dump(mode='json'),
            )
        )
        await session.flush()
        return result

    async def quota(
        self, session: AsyncSession, actor: Principal, user_id: UUID, body: TokenQuotaPatch, *, token_limit: int
    ) -> UsageSummary:
        actor.require_permission('ai:credits:write')
        actor_id = actor.id
        value, previous = await self.command(session, actor_id, 'quota', user_id, body)
        if previous:
            return UsageSummary.model_validate(previous.result)
        await session.execute(
            insert(UsageAccount)
            .values(user_id=user_id, limit_tokens=token_limit, used_tokens=0, reserved_tokens=0)
            .on_conflict_do_nothing(index_elements=['user_id'])
        )
        row = await session.scalar(
            select(UsageAccount)
            .where(UsageAccount.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert row is not None
        if row.quota_version != body.expected_version:
            raise AppError('quota_version_conflict', 'Token 额度已更新，请刷新后重试', 409)
        if body.limit_tokens < row.used_tokens + row.reserved_tokens:
            raise AppError('quota_below_usage', '额度不能低于已使用和预占之和', 409)
        row.limit_tokens = body.limit_tokens
        row.quota_version += 1
        await session.flush()
        result = await self.meter.summary(session, user_id, token_limit=token_limit)
        return await self.remember(session, actor_id, 'quota', body, value, result)

    async def reconcile(
        self, session: AsyncSession, actor: Principal, run_id: UUID, body: TokenReconcile
    ) -> UsageRecord:
        actor.require_permission('ai:credits:write')
        actor_id = actor.id
        value, previous = await self.command(session, actor_id, 'reconcile', run_id, body)
        if previous:
            return UsageRecord.model_validate(previous.result)
        row = await session.scalar(
            select(Invocation)
            .where(Invocation.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.points_enforced:
            raise AppError(
                'token_reconciliation_unavailable', '仅非积分模式调用可在此核对；积分调用请使用积分明细', 409
            )
        if row.version != body.expected_version:
            raise AppError('usage_version_conflict', '调用计量已更新，请刷新后核对', 409)
        if row.status != 'unknown':
            raise AppError('reconciliation_unavailable', '只有未知用量可以进行核对', 409)
        try:
            await self.meter.reconcile(
                session,
                run_id,
                input_tokens=body.input_tokens,
                output_tokens=body.output_tokens,
                search_units=body.search_units,
                cache_tokens=body.cache_tokens,
                rejected_before_execution=body.rejected_before_execution,
            )
        except ValueError as error:
            raise AppError('invalid_usage', '实际用量无效', 422) from error
        return await self.remember(session, actor_id, 'reconcile', body, value, self.meter.record(row))
