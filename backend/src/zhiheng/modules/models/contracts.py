from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
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

MAX_IMAGE_SNAPSHOT_BYTES = 2_000_000
IMAGE_TOKEN_RESERVATION = 2048


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
    def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]: ...


class ProviderError(AppError):
    def __init__(self, *, definitive_rejection: bool = False) -> None:
        super().__init__(
            'provider_rejected' if definitive_rejection else 'provider_unavailable', '模型服务请求失败', 502
        )
        self.definitive_rejection = definitive_rejection


class ModelOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
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
    max_output_tokens: int = Field(strict=True, ge=1, le=131072)
    version: Literal[1] = 1
    connection_revision_id: UUID | None = None
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    vision_enabled: bool = Field(default=False, strict=True)
    options: ModelOptions = Field(default_factory=ModelOptions)

    @model_validator(mode='after')
    def supported_vision(self) -> 'ModelBinding':
        if self.vision_enabled and self.model_type != 'chat':
            raise ValueError('Vision requires chat model')
        return self


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
    enabled: bool
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    vision_enabled: bool = Field(default=False, strict=True)
    published: bool = True
    capabilities: tuple[Literal['chat', 'stream', 'embedding', 'rerank', 'vision'], ...] = ('chat', 'stream')


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
