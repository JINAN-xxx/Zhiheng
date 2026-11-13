"""Public, exact-decimal contracts for platform credits."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


class CreditValue(BaseModel):
    @field_serializer('*', when_used='json', check_fields=False)
    def exact_decimal_json(self, value: object) -> object:
        if isinstance(value, Decimal):
            return format(value.copy_abs() if value.is_zero() else value, 'f')
        return value


class StrictRequest(CreditValue):
    model_config = ConfigDict(extra='forbid')


class CreditRequest(StrictRequest):
    reason: str = Field(default='', max_length=1000)
    client_request_id: str = Field(min_length=1, max_length=128)

    @field_validator('reason', 'client_request_id')
    @classmethod
    def trimmed(cls, value: str) -> str:
        return value.strip()

    @field_validator('client_request_id')
    @classmethod
    def nonblank_request_id(cls, value: str) -> str:
        if not value:
            raise ValueError('A nonblank value is required')
        return value


class CreditWrite(CreditRequest):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator('reason')
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value:
            raise ValueError('A nonblank value is required')
        return value


class CreditState(BaseModel):
    enabled: bool
    version: int


class CreditStatePatch(CreditRequest):
    enabled: bool = Field(strict=True)
    expected_version: int = Field(strict=True, ge=0)


class CreditAccountStatePatch(CreditWrite):
    enabled: bool = Field(strict=True)
    expected_version: int = Field(strict=True, ge=0)


class CreditAdjustment(CreditWrite):
    kind: Literal['grant', 'reclaim']
    points: Decimal = Field(gt=0, max_digits=38, decimal_places=18, allow_inf_nan=False)

    @field_validator('points', mode='before')
    @classmethod
    def decimal_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError('Decimal strings are required')
        return value


class CreditSummary(CreditValue):
    user_id: UUID
    enabled: bool
    version: int
    allocated_points: Decimal
    reclaimed_points: Decimal
    used_points: Decimal
    reserved_points: Decimal
    available_points: Decimal


class CreditPrices(StrictRequest):
    billing_mode: Literal['token', 'search_unit']
    is_free: bool = Field(default=False, strict=True)
    input_points: Decimal = Field(default=Decimal(0), ge=0, max_digits=38, decimal_places=18, allow_inf_nan=False)
    output_points: Decimal = Field(default=Decimal(0), ge=0, max_digits=38, decimal_places=18, allow_inf_nan=False)
    cache_points: Decimal = Field(default=Decimal(0), ge=0, max_digits=38, decimal_places=18, allow_inf_nan=False)
    search_points: Decimal = Field(default=Decimal(0), ge=0, max_digits=38, decimal_places=18, allow_inf_nan=False)

    @model_validator(mode='after')
    def valid_prices(self) -> 'CreditPrices':
        prices = (self.input_points, self.output_points, self.cache_points, self.search_points)
        if self.is_free != all(price == 0 for price in prices):
            raise ValueError('All zero prices require an explicit free rate; free rates must have all zero prices')
        if self.billing_mode == 'token' and self.search_points != 0:
            raise ValueError('Token rates cannot include a search unit price')
        if self.billing_mode == 'search_unit' and any(prices[:3]):
            raise ValueError('Search unit rates cannot include token prices')
        return self


class CreditRateCreate(CreditPrices, CreditRequest):
    @field_validator('input_points', 'output_points', 'cache_points', 'search_points', mode='before')
    @classmethod
    def decimal_string(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError('Decimal strings are required')
        return value


class CreditRateInfo(CreditPrices):
    id: UUID
    model_id: UUID
    version: int
    created_at: datetime


class CreditReconcile(CreditWrite):
    expected_version: int = Field(strict=True, ge=1)
    input_tokens: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    output_tokens: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    cache_tokens: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    search_units: int | None = Field(default=None, strict=True, ge=0, le=2_147_483_647)
    rejected_before_execution: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def confirmed_usage(self) -> 'CreditReconcile':
        if self.input_tokens is not None and (self.cache_tokens or 0) > self.input_tokens:
            raise ValueError('Cached tokens cannot exceed total input tokens')
        if self.rejected_before_execution and any(
            value not in (None, 0)
            for value in (self.input_tokens, self.output_tokens, self.cache_tokens, self.search_units)
        ):
            raise ValueError('A confirmed rejection cannot include consumed usage')
        return self


class CreditReservation(CreditValue):
    run_id: UUID
    user_id: UUID
    model_id: UUID
    source: str
    status: Literal['reserved', 'unknown', 'settled']
    version: int
    reserved_points: Decimal
    charged_points: Decimal | None
    failure_waived: bool = False
    rate_version: int
    created_at: datetime
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    search_units: int | None = None
    reasoning_tokens: int | None = None
    billing_mode: Literal['token', 'search_unit'] = 'token'


class CreditLedgerEntry(CreditValue):
    id: UUID
    user_id: UUID
    operator_id: UUID | None
    kind: str
    amount: Decimal
    available_delta: Decimal
    reserved_delta: Decimal
    available_after: Decimal
    reserved_after: Decimal
    run_id: UUID | None
    reason: str
    created_at: datetime


class UsageOutcome(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    status: Literal['pending', 'known', 'unknown'] = 'pending'
    points_enforced: bool = False


@dataclass(frozen=True)
class MeterReservation:
    """Admission metadata exposed to the unified usage facade without exposing ORM rows."""

    points_enforced: bool
    key_id: UUID | None = None
    key_limit: Decimal | None = None


class UsageRecord(BaseModel):
    version: int = 1
    points_enforced: bool = False
    id: UUID
    run_id: UUID
    model_id: UUID
    source: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    charged_tokens: int | None
    created_at: datetime


class UsageSummary(BaseModel):
    quota_version: int = 0
    points_enabled: bool = False
    total: int = 0
    page: int = 1
    page_size: int = 100
    limit_tokens: int
    used_tokens: int
    reserved_tokens: int
    records: list[UsageRecord]


class ApplicationUsage(BaseModel):
    id: UUID
    run_id: UUID
    application_id: UUID | None
    key_id: UUID | None
    model_id: UUID
    operation: str
    status: str
    points_enforced: bool = False
    input_tokens: int | None
    output_tokens: int | None
    search_units: int | None
    cache_tokens: int | None
    reasoning_tokens: int | None
    billable_cost: Decimal | None
    currency: str | None
    rate_card_id: UUID | None
    rate_version: int | None
    created_at: datetime


@dataclass(frozen=True)
class KeyUsageFacts:
    """Read-only billing facts consumed by Developer Key admission policy."""

    active: int
    spent: Decimal


@dataclass(frozen=True)
class InvocationIdentity:
    """Original invocation authorization identity without exposing the ledger row."""

    application_id: UUID | None
    key_id: UUID | None
    user_id: UUID
    model_id: UUID


class TokenQuotaPatch(CreditWrite):
    expected_version: int = Field(strict=True, ge=0)
    limit_tokens: int = Field(strict=True, ge=1, le=10**12)


class TokenReconcile(CreditReconcile):
    """The same immutable usage facts, applied to Token-only invocations."""
