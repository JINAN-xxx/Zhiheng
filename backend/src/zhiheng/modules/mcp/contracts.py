"""The supported MCP connection, discovery and workspace contracts."""

import json
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

MAX_ARGUMENT_BYTES = 16_384
MAX_SCHEMA_BYTES = 8192


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ServerCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    endpoint: str = Field(min_length=1, max_length=512)
    token: SecretStr | None = Field(default=None, max_length=4096)


class ServerUpdate(ServerCreate):
    expected_version: int = Field(ge=1, strict=True)
    enabled: bool = Field(strict=True)
    clear_token: bool = Field(default=False, strict=True)


class ServerVersion(Input):
    expected_version: int = Field(ge=1, strict=True)


class ToolGrant(ServerVersion):
    enabled: bool = Field(strict=True)
    role_ids: list[UUID] = Field(max_length=50)


class ToolDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=120, pattern=r'^[a-zA-Z0-9_.-]+$')
    description: str = Field(default='', max_length=2000)
    input_schema: dict[str, object]
    read_only: bool

    @field_validator('input_schema')
    @classmethod
    def bounded_schema(cls, value: dict[str, object]) -> dict[str, object]:
        if value.get('type') != 'object' or len(json.dumps(value, allow_nan=False)) > MAX_SCHEMA_BYTES:
            raise ValueError('工具输入必须是有限的 JSON 对象结构')
        return value


class ManagedTool(ToolDefinition):
    enabled: bool = False
    role_ids: list[UUID] = Field(default_factory=list)


class ServerView(BaseModel):
    id: UUID
    name: str
    endpoint: str | None
    enabled: bool
    has_token: bool | None
    version: int
    synced_at: datetime | None
    tools: list[ManagedTool]


class CatalogTool(ToolDefinition):
    server_id: UUID
    server_name: str
    version: int


class ToolSelection(Input):
    server_id: UUID
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1, strict=True)
    arguments: dict[str, object]

    @field_validator('arguments')
    @classmethod
    def bounded_arguments(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            encoded = json.dumps(value, ensure_ascii=True, allow_nan=False)
        except (ValueError, RecursionError) as error:
            raise ValueError('工具参数必须是有效的 JSON 对象') from error
        if len(encoded) > MAX_ARGUMENT_BYTES:
            raise ValueError('工具参数超过 16 KB')
        return value


class BoundTool(ToolSelection):
    request_id: UUID

    @classmethod
    def freeze(cls, selection: ToolSelection, request_id: UUID) -> 'BoundTool':
        return cls.model_validate({**selection.model_dump(), 'request_id': request_id})


class CallView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tool_name: str
    caller_id: UUID
    run_id: UUID
    status: str
    error_code: str | None
    duration_ms: int
    created_at: datetime
