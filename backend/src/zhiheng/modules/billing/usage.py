"""One admission and settlement facade for every platform model invocation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from zhiheng.modules.billing.gate import lock_billing_admission
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.billing.contracts import CreditReconcile, CreditReservation, UsageOutcome, UsageSummary
    from zhiheng.modules.billing.ports import MeterPort
    from zhiheng.modules.billing.service import CreditService
    from zhiheng.modules.models.public import ModelService, RateInfo
    from zhiheng.shared.principal import Principal


class UsageService:
    def __init__(self, credit_service: CreditService, meter: MeterPort) -> None:
        self._credits, self._meter = credit_service, meter

    async def points_enabled(self, session: AsyncSession) -> bool:
        await lock_billing_admission(session)
        return (await self._credits.state(session)).enabled

    async def money_rate(
        self,
        session: AsyncSession,
        models: ModelService,
        model_id: UUID,
        *,
        required: bool = False,
    ) -> RateInfo | None:
        """Optional legacy prices; unbilled admission never freezes or applies them."""
        try:
            return await models.latest_rate(session, model_id)
        except AppError as error:
            if error.code != 'model_not_found':
                raise
            return None

    async def reserve(  # noqa: PLR0913 - unified admission boundary
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
        estimated_input_tokens: int | None = None,
        max_output_tokens: int = 0,
        estimated_search_units: int = 0,
    ) -> None:
        enabled = await self.points_enabled(session)
        if enabled and estimated_input_tokens is None:
            raise AppError('invalid_usage_estimate', '积分调用需要输入和最大输出用量估算', 422)
        # Key -> Token account -> credit account is shared by admission,
        # completion and reconciliation. Failed credit admission also rolls back
        # the old meter and the key's rate-limit reservation.
        async with session.begin_nested():
            reservation = await self._meter.reserve(
                session,
                user_id=user_id,
                run_id=run_id,
                model_id=model_id,
                source=source,
                tokens=tokens if enabled else 0,
                token_limit=token_limit,
                key_id=key_id,
                rate=rate if enabled else None,
                operation=operation,
                points_enforced=enabled,
            )
            if enabled:
                await self._credits.reserve(
                    session,
                    user_id=user_id,
                    run_id=run_id,
                    model_id=model_id,
                    source=source,
                    operation=operation,
                    estimated_input_tokens=estimated_input_tokens or 0,
                    max_output_tokens=max_output_tokens,
                    estimated_search_units=estimated_search_units,
                    key_id=reservation.key_id,
                    key_limit=reservation.key_limit,
                )

    async def require_model_price(self, session: AsyncSession, models: ModelService, model_id: UUID) -> None:
        """Reject unpriced API model steps before accepting durable work; reserve still freezes the actual price."""
        if await self.points_enabled(session) and await self._credits.quote(session, model_id) is None:
            raise AppError('credit_rate_missing', '模型尚未配置积分费率', 409)

    async def preflight(
        self,
        session: AsyncSession,
        models: ModelService,
        *,
        user_id: UUID,
        key_id: UUID,
        model_id: UUID,
        input_tokens: int,
        token_limit: int,
    ) -> None:
        await self.require_model_price(session, models, model_id)
        enabled = await self.points_enabled(session)
        rate = await self.money_rate(session, models, model_id, required=True) if enabled else None
        reservation = await self._meter.preflight(
            session,
            user_id=user_id,
            key_id=key_id,
            tokens=input_tokens if enabled else 0,
            token_limit=token_limit,
            rate=rate,
            points_enforced=enabled,
        )
        if enabled:
            await self._credits.preflight(
                session,
                user_id=user_id,
                model_id=model_id,
                key_id=key_id,
                key_limit=reservation.key_limit,
                input_tokens=input_tokens,
            )

    async def require_reservation(
        self,
        session: AsyncSession,
        run_id: UUID,
        user_id: UUID,
        model_id: UUID,
    ) -> None:
        reservation = await self._meter.require_reservation(session, run_id, user_id, model_id)
        if reservation.points_enforced:
            await self._credits.require_reservation(session, run_id, user_id, model_id)

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
        async with session.begin_nested():
            outcome = await self._meter.settle(
                session,
                run_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                rejected_before_execution=rejected_before_execution,
                search_units=search_units,
                cache_tokens=cache_tokens,
                reasoning_tokens=reasoning_tokens,
            )
            if outcome.points_enforced:
                # Each price settles from its own billing unit. Rerank search
                # units can be known while historical Token diagnostics are not.
                await self._credits.settle(
                    session,
                    run_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    rejected_before_execution=rejected_before_execution,
                    search_units=search_units,
                    cache_tokens=cache_tokens,
                    reasoning_tokens=reasoning_tokens,
                )
            return outcome

    async def failed_chat_waivers(self, session: AsyncSession, *, limit: int = 100) -> list[UUID]:
        return await self._credits.failed_chat_waivers(session, limit=limit)

    async def waive_failed_chat(self, session: AsyncSession, run_id: UUID) -> bool:
        return await self._credits.waive_failed_chat(session, run_id)

    async def reconcile(
        self,
        session: AsyncSession,
        actor: Principal,
        run_id: UUID,
        data: CreditReconcile,
    ) -> CreditReservation:
        actor.require_permission('ai:credits:reconcile')
        async with session.begin_nested():
            await self._meter.reconcile(
                session,
                run_id,
                input_tokens=data.input_tokens,
                output_tokens=data.output_tokens,
                cache_tokens=data.cache_tokens,
                search_units=data.search_units,
                rejected_before_execution=data.rejected_before_execution,
            )
            return await self._credits.reconcile(session, actor, run_id, data)

    async def summary(self, session: AsyncSession, user_id: UUID, *, token_limit: int) -> UsageSummary:
        return await self._meter.summary(session, user_id, token_limit=token_limit)

    async def application_usage(
        self,
        session: AsyncSession,
        application_id: UUID,
        page: int,
        page_size: int,
        operation: str | None,
        status: str | None,
    ) -> dict[str, object]:
        return await self._meter.application_usage(session, application_id, page, page_size, operation, status)
