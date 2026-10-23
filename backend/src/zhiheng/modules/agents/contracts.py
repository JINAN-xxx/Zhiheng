from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zhiheng.modules.skills.public import SkillSlug

MAX_HISTORY_BYTES = 64 * 1024


def safe_text(value: str) -> str:
    if '\x00' in value:
        raise ValueError('NUL is not allowed')
    try:
        value.encode('utf-8')
    except UnicodeError as error:
        raise ValueError('Valid UTF-8 is required') from error
    return value


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=500)
    model_id: UUID
    system_prompt: str = Field(default='', max_length=32000)
    welcome_message: str = Field(default='', max_length=2000)
    skill_slugs: list[SkillSlug] = Field(default_factory=list, max_length=5)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)
    application_id: UUID | None = None
    temperature: float = Field(default=0.7, strict=True, ge=0, le=2, allow_inf_nan=False)
    max_tokens: int = Field(default=2048, strict=True, ge=1, le=131072)

    @field_validator('name', 'description', 'system_prompt', 'welcome_message')
    @classmethod
    def text(cls, value: str) -> str:
        return safe_text(value)

    @model_validator(mode='after')
    def unique_selections(self) -> 'AgentConfig':
        if (
            not self.name.strip()
            or len(set(self.skill_slugs)) != len(self.skill_slugs)
            or len(set(self.knowledge_base_ids)) != len(self.knowledge_base_ids)
        ):
            raise ValueError('Name and unique selections are required')
        return self


class AgentUpdate(AgentConfig):
    expected_version: int = Field(strict=True, ge=1)


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=1)


class AgentInfo(AgentConfig):
    id: UUID
    owner_id: UUID
    version: int
    status: Literal['draft', 'published']
    published_version: int | None
    published_config: AgentConfig | None
    created_at: datetime
    updated_at: datetime


class AgentPage(BaseModel):
    items: list[AgentInfo]
    total: int
    page: int
    page_size: int


class AgentSummary(BaseModel):
    id: UUID
    name: str
    description: str
    published_version: int


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1)

    @field_validator('content')
    @classmethod
    def text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('Nonempty text is required')
        return safe_text(value)


class AgentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    messages: list[AgentMessage] = Field(min_length=1, max_length=96)

    @model_validator(mode='after')
    def history(self) -> 'AgentInput':
        if (
            self.messages[-1].role != 'user'
            or sum(len(item.content.encode('utf-8')) for item in self.messages) > MAX_HISTORY_BYTES
        ):
            raise ValueError('Last message must be user; history must fit 64KiB')
        return self


class PreviewRequest(AgentInput):
    client_request_id: UUID


class AgentPublicRequest(AgentInput):
    stream: bool = Field(default=False, strict=True)
