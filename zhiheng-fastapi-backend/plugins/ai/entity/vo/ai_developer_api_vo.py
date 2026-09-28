import re
import unicodedata
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from plugins.ai.domain.resource_id import validate_resource_id
from plugins.ai.entity.vo.ai_model_catalog_vo import ModelResourceId

ApplicationStatus = Literal['active', 'disabled']
DeveloperKeyStatus = Literal['active', 'retiring', 'revoked', 'expired']
GrantStatus = Literal['active', 'disabled']
FORBIDDEN_TEXT_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})
DECIMAL_PATTERN = re.compile(r'^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$')


def _reject_control_characters(value: object) -> object:
    if isinstance(value, str) and any(
        unicodedata.category(character) in FORBIDDEN_TEXT_CATEGORIES for character in value
    ):
        raise ValueError('文本不能包含控制字符')
    return value


def _parse_http_query_integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError('分页参数必须是整数')
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ValueError('分页参数必须是整数')


def _parse_exact_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if not isinstance(value, str) or DECIMAL_PATTERN.fullmatch(value) is None:
        raise ValueError('金额和比例必须使用十进制字符串')
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError('金额和比例格式无效') from None
    if not parsed.is_finite():
        raise ValueError('金额和比例必须是有限值')
    return parsed


def _validate_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('时间必须包含UTC时区')
    normalized = value.astimezone(timezone.utc)
    if normalized <= datetime.now(timezone.utc):
        raise ValueError('过期时间必须晚于当前时间')
    return normalized


HttpQueryInteger = Annotated[int, BeforeValidator(_parse_http_query_integer)]
ExactDecimal = Annotated[Decimal, BeforeValidator(_parse_exact_decimal)]
SafeName = Annotated[
    str,
    BeforeValidator(_reject_control_characters),
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100),
]
ApplicationCode = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=100,
        pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$',
    ),
]
ApplicationResourceId = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(lambda value: validate_resource_id(value, 'app')),
]
DeveloperKeyResourceId = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(lambda value: validate_resource_id(value, 'key')),
]
GrantResourceId = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(lambda value: validate_resource_id(value, 'grt')),
]
QuotaResourceId = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(lambda value: validate_resource_id(value, 'qta')),
]
PositiveInteger = Annotated[int, Field(strict=True, gt=0)]
NonNegativeInteger = Annotated[int, Field(strict=True, ge=0)]


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        extra='forbid',
    )


class _ResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        from_attributes=True,
        extra='ignore',
    )


class ApplicationCreateModel(_StrictRequestModel):
    application_code: ApplicationCode
    application_name: SafeName


class ApplicationUpdateModel(_StrictRequestModel):
    application_name: SafeName
    expected_version: PositiveInteger


class ApplicationPageQueryModel(_StrictRequestModel):
    application_code: Annotated[str, StringConstraints(strict=True, max_length=100)] | None = None
    application_name: Annotated[str, StringConstraints(strict=True, max_length=100)] | None = None
    status: ApplicationStatus | None = None
    page_num: HttpQueryInteger = Field(default=1, ge=1)
    page_size: HttpQueryInteger = Field(default=10, ge=1, le=100)


class QuotaPolicyWriteModel(_StrictRequestModel):
    rpm_limit: PositiveInteger | None = None
    tpm_limit: PositiveInteger | None = None
    concurrency_limit: PositiveInteger | None = None
    monthly_budget: Annotated[ExactDecimal, Field(ge=0)] | None = None
    warning_ratio: Annotated[ExactDecimal, Field(gt=0, le=1)] = Decimal('0.8000')
    hard_limit_ratio: Annotated[ExactDecimal, Field(gt=0)] = Decimal('1.0000')
    currency: Annotated[str, StringConstraints(strict=True, pattern=r'^[A-Z]{3}$')] = 'USD'

    @model_validator(mode='after')
    def validate_ratios(self) -> 'QuotaPolicyWriteModel':
        if self.hard_limit_ratio < self.warning_ratio:
            raise ValueError('硬限制比例不能低于告警比例')
        return self


class DeveloperKeyCreateModel(_StrictRequestModel):
    key_name: SafeName
    model_ids: list[ModelResourceId] = Field(min_length=1, max_length=100)
    expires_at: Annotated[datetime, AfterValidator(_validate_utc_datetime)] | None = None
    quota: QuotaPolicyWriteModel = Field(default_factory=QuotaPolicyWriteModel)

    @field_validator('model_ids')
    @classmethod
    def require_unique_models(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError('模型授权不能重复')
        return value


class DeveloperKeyRotateModel(_StrictRequestModel):
    grace_period_seconds: NonNegativeInteger = Field(default=86400, le=604800)


class ApplicationModel(_ResponseModel):
    application_id: ApplicationResourceId
    application_code: ApplicationCode
    application_name: SafeName
    owner_user_id: PositiveInteger
    status: ApplicationStatus
    version: PositiveInteger
    disabled_at: datetime | None = None
    deleted_at: datetime | None = None
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class ModelGrantModel(_ResponseModel):
    grant_id: GrantResourceId
    model_id: ModelResourceId
    status: GrantStatus


class QuotaPolicyModel(_ResponseModel):
    quota_id: QuotaResourceId
    rpm_limit: PositiveInteger | None = None
    tpm_limit: PositiveInteger | None = None
    concurrency_limit: PositiveInteger | None = None
    monthly_budget: Decimal | None = None
    warning_ratio: Decimal
    hard_limit_ratio: Decimal
    currency: str
    version: PositiveInteger


class DeveloperKeySummaryModel(_ResponseModel):
    key_id: DeveloperKeyResourceId
    application_id: ApplicationResourceId
    key_name: SafeName
    masked_key: str
    fingerprint: str
    status: DeveloperKeyStatus
    expires_at: datetime | None = None
    retire_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    grants: list[ModelGrantModel]
    quota: QuotaPolicyModel


class DeveloperKeyCreatedModel(DeveloperKeySummaryModel):
    api_key: str = Field(repr=False)


UsageOperationType = Literal['chat', 'embedding', 'rerank']
UsageLedgerStatus = Literal['started', 'completed', 'failed', 'cancelled', 'abandoned']


class UsageLedgerPageQueryModel(_StrictRequestModel):
    operation_type: UsageOperationType | None = None
    status: UsageLedgerStatus | None = None
    page_num: HttpQueryInteger = Field(default=1, ge=1)
    page_size: HttpQueryInteger = Field(default=10, ge=1, le=100)


class UsageLedgerModel(_ResponseModel):
    request_id: str
    api_key_id: DeveloperKeyResourceId
    model_code: str
    operation_type: UsageOperationType
    input_tokens: NonNegativeInteger | None = None
    output_tokens: NonNegativeInteger | None = None
    cache_tokens: NonNegativeInteger | None = None
    reasoning_tokens: NonNegativeInteger | None = None
    search_units: NonNegativeInteger | None = None
    raw_cost: Decimal | None = None
    billable_cost: Decimal | None = None
    currency: str
    cost_estimated: bool
    status: UsageLedgerStatus
    error_code: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class RateCardCreateModel(_StrictRequestModel):
    billing_mode: Literal['token', 'search_unit'] = 'token'
    input_price_per_token: Annotated[ExactDecimal, Field(ge=0)] = Decimal('0')
    output_price_per_token: Annotated[ExactDecimal, Field(ge=0)] = Decimal('0')
    cache_price_per_token: Annotated[ExactDecimal, Field(ge=0)] | None = None
    search_price_per_unit: Annotated[ExactDecimal, Field(ge=0)] | None = None
    currency: Annotated[str, StringConstraints(strict=True, pattern=r'^[A-Z]{3}$')] = 'USD'
    effective_from: datetime | None = None

    @model_validator(mode='after')
    def validate_billing_mode(self) -> 'RateCardCreateModel':
        if self.billing_mode == 'search_unit':
            if self.search_price_per_unit is None or self.search_price_per_unit <= 0:
                raise ValueError('Search Unit 计费模式必须配置正的 search_price_per_unit')
            if (
                self.input_price_per_token != 0
                or self.output_price_per_token != 0
                or self.cache_price_per_token is not None
            ):
                raise ValueError('Search Unit 计费模式不能配置 Token 价格')
        elif self.search_price_per_unit is not None:
            raise ValueError('Token 计费模式不能配置 search_price_per_unit')
        return self


class RateCardModel(_ResponseModel):
    rate_card_id: str
    version: PositiveInteger
    billing_mode: Literal['token', 'search_unit']
    input_price_per_token: Decimal
    output_price_per_token: Decimal
    cache_price_per_token: Decimal | None = None
    search_price_per_unit: Decimal | None = None
    currency: str
    effective_from: datetime
    effective_to: datetime | None = None
    status: Literal['active', 'disabled']


class ModelSmokeTestRequestModel(_StrictRequestModel):
    pass


class ModelSmokeTestResultModel(_ResponseModel):
    model_type: Literal['chat', 'embedding', 'rerank']
    success: bool
    latency_ms: NonNegativeInteger | None = None
    message: str
