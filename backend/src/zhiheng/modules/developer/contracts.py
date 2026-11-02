import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from zhiheng.modules.developer.scopes import INVOKE_SCOPES, RESOURCE_RPM_MAX, ResourceAction, Scope
from zhiheng.shared.time import utcnow


class UsageOutcome(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    status: Literal['pending', 'known', 'unknown'] = 'pending'
    points_enforced: bool = False


@dataclass(frozen=True)
class LegacyReservation:
    """Admission metadata exposed to the unified usage facade without exposing ORM rows."""

    points_enforced: bool
    key_id: UUID | None = None
    key_limit: Decimal | None = None


class UsageRecord(BaseModel):
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
    limit_tokens: int
    used_tokens: int
    reserved_tokens: int
    records: list[UsageRecord]


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ApplicationCreate(StrictRequest):
    code: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$')
    name: str = Field(min_length=1, max_length=80)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(unicodedata.category(c).startswith('C') for c in value):
            raise ValueError('A printable name is required')
        return value


class ApplicationPatch(StrictRequest):
    name: str = Field(min_length=1, max_length=80)
    expected_version: int = Field(strict=True, ge=1)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value: str) -> str:
        return ApplicationCreate.clean_name(value)


class ApplicationInfo(BaseModel):
    id: UUID
    code: str
    name: str
    status: str
    version: int
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    source_file_limit: int = 1000
    source_byte_limit: int = 1073741824


class KeyCreate(StrictRequest):
    name: str = Field(min_length=1, max_length=80)
    model_ids: list[UUID] = Field(default_factory=list, max_length=256)
    scopes: tuple[Scope, ...] = INVOKE_SCOPES
    resource_rpm_limit: int = Field(default=120, strict=True, ge=1, le=RESOURCE_RPM_MAX)

    @model_validator(mode='after')
    def capabilities(self) -> 'KeyCreate':
        if not self.scopes or len(set(self.scopes)) != len(self.scopes):
            raise ValueError('Nonempty unique scopes are required')
        if set(self.scopes).intersection(INVOKE_SCOPES) and not self.model_ids:
            raise ValueError('Invoke scopes require models')
        return self

    rpm_limit: int | None = Field(default=None, strict=True, gt=0)
    tpm_limit: int | None = Field(default=None, strict=True, gt=0)
    concurrency_limit: int | None = Field(default=None, strict=True, gt=0)
    expires_at: datetime | None = None
    monthly_budget: Decimal | None = Field(default=None, ge=0, max_digits=30, decimal_places=12)
    monthly_points_limit: Decimal | None = Field(
        default=None, ge=0, max_digits=38, decimal_places=18, allow_inf_nan=False
    )
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')

    @field_validator('name')
    @classmethod
    def clean_name(cls, value: str) -> str:
        return ApplicationCreate.clean_name(value)

    @field_validator('model_ids')
    @classmethod
    def unique_models(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError('Duplicate models')
        return value

    @field_validator('monthly_budget', 'monthly_points_limit', mode='before')
    @classmethod
    def exact_budget(cls, value: object) -> object:
        if value is not None and not isinstance(value, str):
            raise ValueError('Decimal strings are required')
        return value

    @field_validator('expires_at')
    @classmethod
    def future_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value <= utcnow()):
            raise ValueError('Future aware timestamp is required')
        return value


class KeyRotate(StrictRequest):
    grace_period_seconds: int = Field(default=86400, strict=True, ge=0, le=604800)


class KeyInfo(BaseModel):
    id: UUID
    application_id: UUID
    name: str
    prefix: str
    status: str
    model_ids: list[UUID]
    scopes: tuple[Scope, ...]
    resource_rpm_limit: int
    rpm_limit: int | None
    tpm_limit: int | None
    concurrency_limit: int | None
    monthly_budget: Decimal | None
    monthly_points_limit: Decimal | None = None
    currency: str
    created_at: datetime
    expires_at: datetime | None
    retire_at: datetime | None
    last_used_at: datetime | None

    @field_serializer('monthly_points_limit', when_used='json')
    def points_limit_json(self, value: Decimal | None) -> str | None:
        if value is None:
            return None
        return format(value.copy_abs() if value.is_zero() else value, 'f')


class IssuedKey(KeyInfo):
    api_key: str = Field(repr=False)


class ApplicationUsage(BaseModel):
    id: UUID
    run_id: UUID
    application_id: UUID | None
    key_id: UUID | None
    model_id: UUID
    operation: str
    status: str
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


class ResourceGrantRequest(StrictRequest):
    actions: tuple[ResourceAction, ...] = Field(min_length=1, max_length=4)
    expected_version: int = Field(strict=True, ge=1)

    @field_validator('actions')
    @classmethod
    def unique_actions(cls, value: tuple[ResourceAction, ...]) -> tuple[ResourceAction, ...]:
        if len(set(value)) != len(value):
            raise ValueError('Duplicate resource actions')
        return value


class ResourceGrantRemove(StrictRequest):
    expected_version: int = Field(strict=True, ge=1)


class ResourceQuotaRequest(ResourceGrantRemove):
    source_file_limit: int = Field(strict=True, ge=1)
    source_byte_limit: int = Field(strict=True, ge=1)
