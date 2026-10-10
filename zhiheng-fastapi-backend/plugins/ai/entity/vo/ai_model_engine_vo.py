import re
import unicodedata
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

FieldType = Literal['text', 'password', 'select', 'upload', 'textarea']
SimpleJSONValue = str | int | float | bool | None
_CREDENTIAL_NAME_PATTERN = re.compile(r'^[A-Za-z0-9._-]{1,128}$')
_CREDENTIAL_FIELD_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,127}$')
_FORBIDDEN_TEXT_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})


def _contains_forbidden_text(value: str) -> bool:
    return any(unicodedata.category(character) in _FORBIDDEN_TEXT_CATEGORIES for character in value)


class _InvalidCredentialValues:
    __slots__ = ()

    def __repr__(self) -> str:
        return '<invalid credential values>'


_INVALID_CREDENTIAL_VALUES = _InvalidCredentialValues()


def _validate_secret_values(value: object) -> dict[str, SecretStr] | _InvalidCredentialValues:
    if not isinstance(value, Mapping):
        return _INVALID_CREDENTIAL_VALUES
    validated: dict[str, SecretStr] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str) or _CREDENTIAL_FIELD_PATTERN.fullmatch(key) is None:
            return _INVALID_CREDENTIAL_VALUES
        if not isinstance(raw_value, str):
            return _INVALID_CREDENTIAL_VALUES
        validated[key] = SecretStr(raw_value)
    return validated


def _reject_control_characters(value: object) -> object:
    if isinstance(value, str) and _contains_forbidden_text(value):
        raise ValueError('字段不能包含控制字符')
    return value


CredentialName = Annotated[
    str,
    StringConstraints(strict=True, pattern=r'^[A-Za-z0-9._-]{1,128}$'),
]
SafeIdentifier = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255),
    BeforeValidator(_reject_control_characters),
]
Description = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000),
    BeforeValidator(_reject_control_characters),
]
PositiveTimeout = Annotated[float, Field(strict=True, gt=0, le=600)]
RetryCount = Annotated[int, Field(strict=True, ge=0, le=10)]
RateLimit = Annotated[int, Field(strict=True, ge=1, le=10_000_000_000)]
NonNegativeFloat = Annotated[float, Field(strict=True, ge=0)]
NonNegativeInteger = Annotated[int, Field(strict=True, ge=0)]


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        extra='forbid',
        hide_input_in_errors=True,
    )


class _StrictResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        from_attributes=True,
        extra='forbid',
    )


class _CredentialValuesRequestModel(_StrictRequestModel):
    values: dict[str, SecretStr] = Field(default_factory=dict, repr=False)

    @field_validator('values', mode='before')
    @classmethod
    def validate_values(cls, value: object) -> object:
        return _validate_secret_values(value)


class _CredentialWriteRequestModel(_CredentialValuesRequestModel):
    values: dict[str, SecretStr] = Field(min_length=1, repr=False)


class ProviderFieldModel(_StrictResponseModel):
    field_key: str
    label: str
    placeholder: str | None = None
    tooltip: str | None = None
    required: bool
    field_type: FieldType
    options: list[SimpleJSONValue] | None = None
    default_value: SimpleJSONValue = None


class ProviderModel(_StrictResponseModel):
    provider: str
    display_name: str
    litellm_provider: str
    default_model_placeholder: str | None = None
    credential_fields: list[ProviderFieldModel]


class ConnectionProfileModel(_StrictResponseModel):
    status: Literal['ready', 'missing', 'conflict', 'invalid']
    api_base: str | None = None
    provider_ids: list[str] = Field(default_factory=list)


class CredentialSummaryModel(_StrictResponseModel):
    credential_name: CredentialName
    provider: str
    referenced_deployment_count: NonNegativeInteger | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    connection_profile: ConnectionProfileModel | None = None


class CredentialCreateModel(_CredentialWriteRequestModel):
    credential_name: CredentialName
    provider: SafeIdentifier


class CredentialRotateModel(_CredentialWriteRequestModel):
    provider: SafeIdentifier


class DeploymentModel(_StrictResponseModel):
    deployment_id: str
    model_alias: str
    provider: str
    upstream_model: str
    credential_name: CredentialName | None = None
    status: str
    latency_ms: NonNegativeFloat | None = None
    blocked: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None
    timeout: PositiveTimeout | None = None
    stream_timeout: PositiveTimeout | None = None
    max_retries: RetryCount | None = None
    rpm: RateLimit | None = None
    tpm: RateLimit | None = None
    description: str | None = None


class DeploymentCreateModel(_StrictRequestModel):
    model_alias: SafeIdentifier
    upstream_model: SafeIdentifier
    provider: SafeIdentifier
    credential_name: CredentialName
    model_type: Literal['chat', 'embedding', 'rerank'] | None = None
    timeout: PositiveTimeout | None = None
    stream_timeout: PositiveTimeout | None = None
    max_retries: RetryCount | None = None
    rpm: RateLimit | None = None
    tpm: RateLimit | None = None
    description: Description | None = None


class DeploymentUpdateModel(_StrictRequestModel):
    model_alias: SafeIdentifier | None = None
    upstream_model: SafeIdentifier | None = None
    provider: SafeIdentifier | None = None
    credential_name: CredentialName | None = None
    timeout: PositiveTimeout | None = None
    stream_timeout: PositiveTimeout | None = None
    max_retries: RetryCount | None = None
    rpm: RateLimit | None = None
    tpm: RateLimit | None = None
    description: Description | None = None
    blocked: bool | None = Field(default=None, strict=True)

    @model_validator(mode='before')
    @classmethod
    def validate_patch_shape(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        if not value:
            raise ValueError('至少需要一个更新字段')
        non_nullable = {
            'modelAlias',
            'upstreamModel',
            'provider',
            'credentialName',
            'timeout',
            'streamTimeout',
            'maxRetries',
            'rpm',
            'tpm',
            'description',
            'blocked',
        }
        if any(key in value and value[key] is None for key in non_nullable):
            raise ValueError('部署更新字段不允许为 null')
        return value


class ConnectionTestRequestModel(_CredentialValuesRequestModel):
    upstream_model: SafeIdentifier
    provider: SafeIdentifier
    model_type: Literal['chat', 'embedding', 'rerank'] | None = None
    credential_name: CredentialName | None = None


class ConnectionTestResultModel(_StrictResponseModel):
    success: bool
    latency_ms: NonNegativeFloat | None = None
    message: str
    failure_reason: (
        Literal[
            'timeout',
            'network',
            'authentication',
            'permission',
            'quota',
            'rate_limit',
            'model_not_found',
            'invalid_request',
            'upstream_unavailable',
            'unknown',
        ]
        | None
    ) = None


class ModelEngineStatusModel(_StrictResponseModel):
    runtime_configured: bool
    runtime_available: bool
    management_configured: bool
    management_available: bool
    latency_ms: NonNegativeFloat | None = None
    last_checked_at: datetime | None = None
    deployment_count: NonNegativeInteger | None = None
    credential_count: NonNegativeInteger | None = None
