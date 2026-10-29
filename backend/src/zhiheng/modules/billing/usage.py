"""One admission and settlement facade for every platform model invocation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from zhiheng.modules.billing.gate import lock_billing_admission
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.billing.contracts import CreditReconcile, CreditReservation
    from zhiheng.modules.billing.ports import LegacyMeterPort
    from zhiheng.modules.billing.service import CreditService
    from zhiheng.modules.developer.public import UsageOutcome, UsageSummary
    from zhiheng.modules.models.public import ModelService, RateInfo
    from zhiheng.shared.principal import Principal


class UsageService:
    def __init__(self, credit_service: CreditService, legacy: LegacyMeterPort) -> None:
        self._credits, self._legacy = credit_service, legacy

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
        """Keep historical currency prices optional once points govern admission."""
        try:
            return await models.latest_rate(session, model_id)
        except AppError as error:
            if error.code != 'model_not_found' or (required and not await self.points_enabled(session)):
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
        if not enabled and key_id is not None and rate is None:
            raise AppError('model_not_found', 'Model is unavailable', 404)
        # Key -> legacy account -> credit account is shared by admission,
        # completion and reconciliation. Failed credit admission also rolls back
        # the old meter and the key's rate-limit reservation.
        async with session.begin_nested():
            reservation = await self._legacy.reserve(
                session,
                user_id=user_id,
                run_id=run_id,
                model_id=model_id,
                source=source,
                tokens=tokens,
                token_limit=token_limit,
                key_id=key_id,
                rate=rate,
                operation=operation,
                points_enforced=enabled,
                enforce_token_limit=not enabled,
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

    async def require_reservation(
        self,
        session: AsyncSession,
        run_id: UUID,
        user_id: UUID,
        model_id: UUID,
    ) -> None:
        reservation = await self._legacy.require_reservation(session, run_id, user_id, model_id)
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
            outcome = await self._legacy.settle(
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

    async def reconcile(
        self,
        session: AsyncSession,
        actor: Principal,
        run_id: UUID,
        data: CreditReconcile,
    ) -> CreditReservation:
        actor.require_permission('ai:credits:write')
        async with session.begin_nested():
            await self._legacy.reconcile(
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
        return await self._legacy.summary(session, user_id, token_limit=token_limit)

    async def application_usage(
        self,
        session: AsyncSession,
        application_id: UUID,
        page: int,
        page_size: int,
        operation: str | None,
        status: str | None,
    ) -> dict[str, object]:
        return await self._legacy.application_usage(session, application_id, page, page_size, operation, status)
