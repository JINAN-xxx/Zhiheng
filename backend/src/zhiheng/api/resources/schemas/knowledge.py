"""knowledge resource wire DTOs."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from zhiheng.modules.knowledge.public import (
    BaseCreate,
    BaseInfo,
    BasePatch,
    ChunkParameters,
    DocumentInfo,
    FolderCreate,
    FolderInfo,
    QueryRequest,
)


class CreateRequest(ChunkParameters):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    embed_model_id: UUID

    @field_validator('name', 'description')
    @classmethod
    def _text(cls, value: str) -> str:
        return BaseCreate.text(value)


class PatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator('name', 'description')
    @classmethod
    def _text(cls, value: str | None, info: ValidationInfo) -> str | None:
        return BasePatch.text(value, info)


class FolderPatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    _name = field_validator('name')(FolderCreate._valid_name)


class ImportRequest(ChunkParameters):
    file_ids: list[UUID] = Field(min_length=1, max_length=20)
    folder_id: UUID | None = None


class QueryBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=8, strict=True, ge=1, le=8)
    similarity_threshold: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)

    @field_validator('query')
    @classmethod
    def _text(cls, value: str) -> str:
        return QueryRequest.text(value)


class BasePage(BaseModel):
    items: list[BaseInfo]
    next_cursor: str | None
    has_more: bool


class FolderPage(BaseModel):
    items: list[FolderInfo]
    next_cursor: str | None
    has_more: bool


class DocumentPage(BaseModel):
    items: list[DocumentInfo]
    next_cursor: str | None
    has_more: bool
