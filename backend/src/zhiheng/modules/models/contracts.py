from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from zhiheng.shared.errors import AppError


@dataclass(frozen=True)
class ModelMessage:
    role: Literal['system', 'user', 'assistant']
    content: str


@dataclass(frozen=True)
class ModelDelta:
    text: str = ''
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: Literal['stop', 'length'] | None = None


class ModelProvider(Protocol):
    def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]: ...


class ProviderError(AppError):
    def __init__(self, *, definitive_rejection: bool = False) -> None:
        super().__init__(
            'provider_rejected' if definitive_rejection else 'provider_unavailable', '模型服务请求失败', 502
        )
        self.definitive_rejection = definitive_rejection


class ModelBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    model_id: UUID
    connection_id: UUID
    upstream_model: str
    max_output_tokens: int = Field(ge=1, le=8192)
    version: Literal[1] = 1


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


class ModelInfo(BaseModel):
    id: UUID
    name: str
    connection_id: UUID
    upstream_model: str
    max_output_tokens: int
    enabled: bool
    capabilities: tuple[Literal['chat', 'stream'], ...] = ('chat', 'stream')
