from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from zhiheng.shared.errors import AppError


@dataclass(frozen=True)
class ModelMessage:
    role: Literal['system', 'developer', 'user', 'assistant']
    content: str


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


class ModelBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    model_id: UUID
    connection_id: UUID
    upstream_model: str
    max_output_tokens: int = Field(ge=1, le=8192)
    version: Literal[1] = 1
    connection_revision_id: UUID | None = None
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    options: ModelOptions = Field(default_factory=ModelOptions)


class ModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    binding: ModelBinding
    messages: tuple[ModelMessage, ...]


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
    published: bool = True
    capabilities: tuple[Literal['chat', 'stream', 'embedding', 'rerank'], ...] = ('chat', 'stream')


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
