"""Developer Key admission policy; billing is accessed through read-only facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from zhiheng.modules.developer.contracts import KeyMeteringReservation
from zhiheng.modules.developer.keys import KeyService
from zhiheng.modules.developer.limits import check_limits, reserve_limits, settle_minute
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from decimal import Decimal
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.billing.public import UsageFactsPort
    from zhiheng.modules.identity.public import IdentityService
    from zhiheng.modules.models.public import RateInfo


class KeyMeteringPolicy:
    def __init__(self, facts: UsageFactsPort, identity: IdentityService) -> None:
        self._facts, self._identity = facts, identity

    async def preflight(
        self,
        session: AsyncSession,
        key_id: UUID,
        tokens: int,
        rate: RateInfo | None,
        *,
        enforce_usage_limits: bool = True,
    ) -> KeyMeteringReservation:
        key = await session.get(DeveloperKey, key_id, populate_existing=True)
        if key is None:
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        await check_limits(session, key, tokens, rate, facts=self._facts, enforce_usage_limits=enforce_usage_limits)
        return KeyMeteringReservation(key.id, key.application_id, key.monthly_points_limit)

    async def reserve(
        self,
        session: AsyncSession,
        key_id: UUID,
        tokens: int,
        rate: RateInfo | None,
        *,
        enforce_usage_limits: bool = True,
    ) -> KeyMeteringReservation:
        key = await session.scalar(
            select(DeveloperKey)
            .where(DeveloperKey.id == key_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if key is None:
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        minute, month, cost = await reserve_limits(
            session, key, tokens, rate, facts=self._facts, enforce_usage_limits=enforce_usage_limits
        )
        return KeyMeteringReservation(key.id, key.application_id, key.monthly_points_limit, minute, month, cost)

    async def require_reservation(
        self,
        session: AsyncSession,
        key_id: UUID,
        application_id: UUID | None,
        model_id: UUID,
    ) -> Decimal | None:
        key = await session.get(DeveloperKey, key_id)
        app = await session.get(DeveloperApplication, application_id)
        if (
            key is None
            or not KeyService.usable(key)
            or app is None
            or (app.status != 'active' or not app.platform_enabled)
            or app.deleted_at is not None
            or str(model_id) not in key.model_ids
            or not await self._identity.is_active(session, app.owner_id)
        ):
            raise AppError('invocation_unavailable', '调用授权已失效', 401)
        return key.monthly_points_limit

    async def lock(self, session: AsyncSession, key_id: UUID) -> None:
        await session.scalar(select(DeveloperKey).where(DeveloperKey.id == key_id).with_for_update())

    async def settle_minute(
        self,
        session: AsyncSession,
        *,
        key_id: UUID,
        quota_minute: int | None,
        reserved_tokens: int,
        tokens: int,
    ) -> None:
        await settle_minute(
            session,
            key_id=key_id,
            quota_minute=quota_minute,
            reserved_tokens=reserved_tokens,
            tokens=tokens,
        )
