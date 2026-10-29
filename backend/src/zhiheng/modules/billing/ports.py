"""Public historical-meter boundary injected by the composition root."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.developer.public import LegacyReservation, UsageOutcome, UsageSummary
    from zhiheng.modules.models.public import RateInfo


class LegacyMeterPort(Protocol):
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
    ) -> LegacyReservation: ...

    async def require_reservation(
        self,
        session: AsyncSession,
        run_id: UUID,
        user_id: UUID,
        model_id: UUID,
    ) -> LegacyReservation: ...

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
    ) -> UsageOutcome: ...

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
    ) -> UsageOutcome: ...

    async def summary(self, session: AsyncSession, user_id: UUID, *, token_limit: int) -> UsageSummary: ...

    async def application_usage(
        self,
        session: AsyncSession,
        application_id: UUID,
        page: int,
        page_size: int,
        operation: str | None,
        status: str | None,
    ) -> dict[str, object]: ...
