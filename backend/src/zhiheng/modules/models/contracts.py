from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from typing import Literal, Protocol, get_args
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from zhiheng.shared.errors import AppError
from zhiheng.shared.numbers import MAX_SAFE_INTEGER

MAX_IMAGE_SNAPSHOT_BYTES = 2_000_000
MAX_TRIAL_PROMPT_CHARS = 4000
MAX_TRIAL_DOCUMENT_CHARS = 1000
MAX_TRIAL_DOCUMENTS = 8
IMAGE_TOKEN_RESERVATION = 2048
MIN_HTTP_STATUS = 100
MAX_HTTP_STATUS = 599


@dataclass(frozen=True)
class ModelImage:
    data_url: str
    estimated_tokens: int = IMAGE_TOKEN_RESERVATION


@dataclass(frozen=True)
class ModelMessage:
    role: Literal['system', 'developer', 'user', 'assistant']
    content: str
    images: tuple[ModelImage, ...] = ()


@dataclass(frozen=True)
class ModelDelta:
    text: str = ''
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: Literal['stop', 'length'] | None = None
    result: dict[str, object] | None = None
    search_units: int | None = None
    cache_tokens: int | None = None
    reasoning_tokens: int | None = None
    reasoning: str = ''


class ModelProvider(Protocol):
    async def validate(self) -> None: ...

    def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]: ...


ProviderFailureReason = Literal[
    'unknown',
    'http_error',
    'insufficient_balance',
    'transport_error',
    'timeout',
    'api_error',
    'invalid_response',
    'incomplete_stream',
    'invalid_content',
    'invalid_reasoning',
    'unsupported_finish_reason',
    'invalid_options',
    'invalid_request',
    'projection_limit',
]
ProviderExceptionType = Literal[
    'APIStatusError',
    'APITimeoutError',
    'APIConnectionError',
    'APIError',
    'HTTPStatusError',
    'TimeoutException',
    'HTTPError',
    'TimeoutError',
    'ValueError',
    'KeyError',
]
ProviderRateLimitMetric = Literal['unknown', 'rpm', 'rph', 'rpd', 'tpm', 'tpd', 'ipm', 'ipd']


class ProviderError(AppError):
    def __init__(
        self,
        *,
        definitive_rejection: bool = False,
        reason: ProviderFailureReason = 'unknown',
        upstream_status: int | None = None,
        upstream_error_type: ProviderExceptionType | None = None,
        rate_limit_metric: ProviderRateLimitMetric = 'unknown',
    ) -> None:
        self.definitive_rejection = definitive_rejection
        # Internal diagnostics are allowlisted even when a caller bypasses typing.
        # Never accept exception text, response bodies, or arbitrary class names.
        self.reason = reason if type(reason) is str and reason in get_args(ProviderFailureReason) else 'unknown'
        self.upstream_status = (
            upstream_status
            if type(upstream_status) is int and MIN_HTTP_STATUS <= upstream_status <= MAX_HTTP_STATUS
            else None
        )
        self.upstream_error_type = (
            upstream_error_type
            if type(upstream_error_type) is str and upstream_error_type in get_args(ProviderExceptionType)
            else None
        )
        self.rate_limit_metric: ProviderRateLimitMetric = (
            rate_limit_metric
            if self.upstream_status == HTTPStatus.TOO_MANY_REQUESTS
            and type(rate_limit_metric) is str
            and rate_limit_metric in get_args(ProviderRateLimitMetric)
            else 'unknown'
        )
        super().__init__(
            'provider_rejected' if definitive_rejection else 'provider_unavailable', '模型调用失败，请稍后重试。', 502
        )


class ModelTrialInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    prompt: str = Field(min_length=1, max_length=MAX_TRIAL_PROMPT_CHARS)
    documents: tuple[str, ...] = Field(default=(), max_length=MAX_TRIAL_DOCUMENTS)

    @field_validator('prompt')
    @classmethod
    def clean_prompt(cls, value: str) -> str:
        value.encode('utf-8')
        if not value.strip() or '\x00' in value:
            raise ValueError('请输入有效的试用内容')
        return value.strip()

    @field_validator('documents')
    @classmethod
    def clean_documents(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        result = []
        for value in values:
            value.encode('utf-8')
            if not value.strip() or '\x00' in value or len(value) > MAX_TRIAL_DOCUMENT_CHARS:
                raise ValueError('每份候选资料需为 1 至 1000 个字符')
            result.append(value.strip())
        return tuple(result)


class ModelOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    provider_output_default: bool = False
    temperature: float | None = None
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    stop: str | tuple[str, ...] | None = None
    user: str | None = None
    dimensions: int | None = None
    top_n: int | None = None
    return_documents: bool | None = None
    response_format: Literal['json_object'] | None = None
    reasoning_effort: Literal['none', 'minimal', 'low', 'medium', 'high'] | None = None

    @model_serializer(mode='wrap')
    def serialize_existing_options(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        raw = handler(self)
        if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
            raise ValueError('Invalid options serialization')
        result: dict[str, object] = dict(raw)
        if not self.provider_output_default:
            result.pop('provider_output_default', None)
        # Optional additions must not invalidate already published vector
        # generations whose schema-7 binding contains the original fields.
        if self.response_format is None:
            result.pop('response_format', None)
        if self.reasoning_effort is None:
            result.pop('reasoning_effort', None)
        return result


class ModelBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    model_id: UUID
    connection_id: UUID
    upstream_model: str
    max_output_tokens: int = Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)
    context_window_tokens: int | None = Field(default=None, strict=True, ge=256, le=2097152)
    version: Literal[1] = 1
    connection_revision_id: UUID | None = None
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    vision_enabled: bool = Field(default=False, strict=True)
    options: ModelOptions = Field(default_factory=ModelOptions)

    @property
    def output_limit_tokens(self) -> int | None:
        return None if self.options.provider_output_default else self.max_output_tokens

    @property
    def output_reservation_tokens(self) -> int:
        # A provisional billing estimate does not become a provider generation limit.
        return min(4096, self.max_output_tokens) if self.options.provider_output_default else self.max_output_tokens

    @model_serializer(mode='wrap')
    def serialize_compatible_binding(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        raw = handler(self)
        if not isinstance(raw, dict):
            raise ValueError('Invalid binding serialization')
        result: dict[str, object] = dict(raw)
        if self.context_window_tokens is None:
            result.pop('context_window_tokens', None)
        return result

    @model_validator(mode='after')
    def supported_vision(self) -> 'ModelBinding':
        if self.vision_enabled and self.model_type != 'chat':
            raise ValueError('Vision requires chat model')
        return self


class ProviderFactory(Protocol):
    def __call__(
        self, *, base_url: str, api_key: str, binding: ModelBinding, timeout_seconds: float
    ) -> ModelProvider: ...


class ModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    binding: ModelBinding
    messages: tuple[ModelMessage, ...]
    execution_timeout_seconds: float | None = Field(default=None, ge=0.1, le=600)
    execution_deadline: datetime | None = None

    @field_validator('messages')
    @classmethod
    def validate_images(cls, messages: tuple[ModelMessage, ...]) -> tuple[ModelMessage, ...]:
        for message in messages:
            for image in message.images:
                if (
                    message.role != 'user'
                    or not image.data_url.startswith('data:image/jpeg;base64,')
                    or len(image.data_url) > MAX_IMAGE_SNAPSHOT_BYTES
                    or image.estimated_tokens != IMAGE_TOKEN_RESERVATION
                ):
                    raise ValueError('Invalid managed image snapshot')
        return messages

    @model_validator(mode='after')
    def require_vision_capability(self) -> 'ModelRequest':
        if any(message.images for message in self.messages) and not self.binding.vision_enabled:
            raise ValueError('Model does not support vision')
        if self.binding.model_type != 'chat' and (
            self.binding.options.response_format is not None or self.binding.options.reasoning_effort is not None
        ):
            raise ValueError('Chat generation controls require chat model')
        return self


class ConnectionInfo(BaseModel):
    id: UUID
    name: str
    provider: Literal['openai_compatible'] = 'openai_compatible'
    base_url: str
    has_api_key: bool = True
    enabled: bool = True


class ModelInfo(BaseModel):
    id: UUID
    name: str
    connection_id: UUID
    upstream_model: str
    max_output_tokens: int
    context_window_tokens: int | None = None
    enabled: bool
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    vision_enabled: bool = Field(default=False, strict=True)
    capabilities: tuple[Literal['chat', 'stream', 'embedding', 'rerank', 'vision'], ...] = ('chat', 'stream')


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    name: str = Field(min_length=1, max_length=80)
    connection_id: UUID
    upstream_model: str = Field(min_length=1, max_length=200)
    max_output_tokens: int = Field(default=512, strict=True, ge=1, le=MAX_SAFE_INTEGER)
    context_window_tokens: int | None = Field(default=None, strict=True, ge=256, le=2097152)
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    vision_enabled: bool = Field(default=False, strict=True)


@dataclass(frozen=True)
class ModelSaveCheck:
    configuration: ModelConfiguration
    binding: ModelBinding
    previous: ModelInfo | None
    enabled: bool | None
    provider: ModelProvider


class RateCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    billing_mode: Literal['token', 'search_unit']
    input_price: Decimal = Field(default=Decimal(0), ge=0, max_digits=30, decimal_places=12)
    output_price: Decimal = Field(default=Decimal(0), ge=0, max_digits=30, decimal_places=12)
    cache_price: Decimal = Field(default=Decimal(0), ge=0, max_digits=30, decimal_places=12)
    search_price: Decimal = Field(default=Decimal(0), ge=0, max_digits=30, decimal_places=12)
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')

    @field_validator('input_price', 'output_price', 'cache_price', 'search_price', mode='before')
    @classmethod
    def exact_price(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError('Decimal strings are required')
        return value


class RateInfo(RateCreate):
    @field_validator('input_price', 'output_price', 'cache_price', 'search_price', mode='before')
    @classmethod
    def exact_price(cls, value: object) -> object:
        return value

    id: UUID
    model_id: UUID
    version: int
    created_at: datetime
