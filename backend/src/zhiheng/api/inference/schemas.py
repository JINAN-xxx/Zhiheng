"""Existing inference wire DTOs; model execution stays in application."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zhiheng.shared.numbers import MAX_SAFE_INTEGER

MAX_STOPS = 4
MAX_ITEMS = 256
MAX_ITEM_BYTES = 32768
MAX_QUERY_BYTES = 8192


class PublicBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: UUID
    user: str | None = Field(default=None, max_length=256)


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['system', 'developer', 'user', 'assistant']
    content: str = Field(min_length=1, max_length=64000)


class ChatBody(PublicBody):
    messages: list[Message] = Field(min_length=1, max_length=100)
    stream: bool = Field(default=False, strict=True)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, strict=True, gt=0, le=MAX_SAFE_INTEGER)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    stop: str | list[str] | None = None

    @field_validator('stop')
    @classmethod
    def validate_stop(cls, value: str | list[str] | None) -> str | list[str] | None:
        if isinstance(value, list) and (
            not 1 <= len(value) <= MAX_STOPS or any(not item or len(item) > MAX_ITEMS for item in value)
        ):
            raise ValueError('Invalid stop sequences')
        if isinstance(value, str) and (not value or len(value) > MAX_ITEMS):
            raise ValueError('Invalid stop sequence')
        return value


class EmbeddingBody(PublicBody):
    input: str | list[str]
    encoding_format: Literal['float'] = 'float'
    dimensions: int | None = Field(default=None, strict=True, ge=1, le=65536)

    @field_validator('input')
    @classmethod
    def validate_input(cls, value: str | list[str]) -> str | list[str]:
        values = [value] if isinstance(value, str) else value
        if not 1 <= len(values) <= MAX_ITEMS or any(not item or len(item.encode()) > MAX_ITEM_BYTES for item in values):
            raise ValueError('Invalid embedding input')
        return value


class Document(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str


class RerankBody(PublicBody):
    query: str = Field(min_length=1)
    documents: list[str | Document] = Field(min_length=1, max_length=256)
    top_n: int | None = Field(default=None, strict=True, ge=1)
    return_documents: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def validate_documents(self) -> 'RerankBody':
        if len(self.query.encode()) > MAX_QUERY_BYTES or (self.top_n is not None and self.top_n > len(self.documents)):
            raise ValueError('Invalid rerank input')
        if any(
            not (text := doc if isinstance(doc, str) else doc.text) or len(text.encode()) > MAX_ITEM_BYTES
            for doc in self.documents
        ):
            raise ValueError('Invalid rerank documents')
        return self
