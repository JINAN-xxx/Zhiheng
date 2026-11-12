"""Public metering, read-only usage facts, and Key access-policy boundaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from decimal import Decimal
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.billing.contracts import (
        InvocationIdentity,
        KeyUsageFacts,
        MeterReservation,
        UsageOutcome,
        UsageSummary,
    )
    from zhiheng.modules.developer.public import KeyMeteringReservation
    from zhiheng.modules.models.public import RateInfo


class MeterPort(Protocol):
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
    ) -> MeterReservation: ...

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
    ) -> MeterReservation: ...

    async def require_reservation(
        self,
        session: AsyncSession,
        run_id: UUID,
        user_id: UUID,
        model_id: UUID,
    ) -> MeterReservation: ...

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


class UsageFactsPort(Protocol):
    async def key_usage(
        self,
        session: AsyncSession,
        key_id: UUID,
        *,
        month: str,
        currency: str,
    ) -> KeyUsageFacts: ...

    async def invocation_identity(self, session: AsyncSession, run_id: UUID) -> InvocationIdentity | None: ...


class KeyMeteringPolicyPort(Protocol):
    async def preflight(
        self,
        session: AsyncSession,
        key_id: UUID,
        tokens: int,
        rate: RateInfo | None,
        *,
        enforce_usage_limits: bool = True,
    ) -> KeyMeteringReservation: ...

    async def reserve(
        self,
        session: AsyncSession,
        key_id: UUID,
        tokens: int,
        rate: RateInfo | None,
        *,
        enforce_usage_limits: bool = True,
    ) -> KeyMeteringReservation: ...

    async def require_reservation(
        self,
        session: AsyncSession,
        key_id: UUID,
        application_id: UUID | None,
        model_id: UUID,
    ) -> Decimal | None: ...

    async def lock(self, session: AsyncSession, key_id: UUID) -> None: ...

    async def settle_minute(
        self,
        session: AsyncSession,
        *,
        key_id: UUID,
        quota_minute: int | None,
        reserved_tokens: int,
        tokens: int,
    ) -> None: ...
