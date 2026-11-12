import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from zhiheng.modules.developer.scopes import INVOKE_SCOPES, RESOURCE_RPM_MAX, ResourceAction, Scope
from zhiheng.shared.time import utcnow


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ApplicationCreate(StrictRequest):
    code: str | None = Field(
        default=None,
        pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$',
        description='省略时由系统生成；保留已有调用方的编码兼容。',
    )
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
    platform_enabled: bool = True
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
    source_file_limit: int = Field(strict=True, ge=1, le=2147483647)
    source_byte_limit: int = Field(strict=True, ge=1, le=9007199254740991)


@dataclass(frozen=True)
class KeyMeteringReservation:
    """Key policy decision returned to billing without exposing access-policy storage."""

    key_id: UUID
    application_id: UUID
    key_limit: Decimal | None
    quota_minute: int | None = None
    quota_month: str | None = None
    reserved_cost: Decimal | None = None


class ApplicationGovernanceInfo(BaseModel):
    id: UUID
    name: str
    owner_id: UUID
    owner_enabled: bool
    platform_enabled: bool
    version: int
    source_file_limit: int
    source_byte_limit: int
    source_count: int
    source_bytes: int


class ApplicationGovernancePage(BaseModel):
    items: list[ApplicationGovernanceInfo]
    total: int
    page: int
    page_size: int


class ApplicationGovernanceState(StrictRequest):
    enabled: bool = Field(strict=True)
    expected_version: int = Field(strict=True, ge=1)
