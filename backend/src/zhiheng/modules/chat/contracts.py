from datetime import datetime
from typing import Literal, TypedDict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from zhiheng.modules.files.public import AttachmentSummary
from zhiheng.modules.knowledge.public import Hit
from zhiheng.shared.numbers import MAX_SAFE_INTEGER


class ConversationInfo(BaseModel):
    id: UUID
    title: str
    version: int = 1
    archived: bool = False
    preparation_id: UUID | None = None
    created_at: datetime
    last_activity_at: datetime | None = None


class ChatContextInfo(BaseModel):
    status: Literal['full', 'summarizing', 'summarized', 'degraded'] = 'full'
    summary_version: int = 0
    covered_rounds: int = 0
    retained_rounds: int = 0
    dirty: bool = False
    truncated: bool = False
    dropped_rounds: int = 0
    estimated_input_tokens: int | None = None
    input_budget: int | None = None
    reason: str | None = None


class SkillSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    slug: str
    name: str
    revision_id: UUID
    content_hash: str


class ChatGenerationValues(TypedDict):
    system_prompt: str
    temperature: float | None
    max_output_tokens: int | None


class ChatGenerationSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    system_prompt: str = Field(default='', strict=True, max_length=32000)
    temperature: float | None = Field(default=None, strict=True, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int | None = Field(default=None, strict=True, ge=1, le=MAX_SAFE_INTEGER)

    @field_validator('system_prompt')
    @classmethod
    def encodable_prompt(cls, value: str) -> str:
        value.encode('utf-8')
        if '\x00' in value:
            raise ValueError('Invalid system prompt')
        return value

    def generation_settings(self) -> ChatGenerationValues:
        return {
            'system_prompt': self.system_prompt,
            'temperature': self.temperature,
            'max_output_tokens': self.max_output_tokens,
        }


class ChatGenerationConfig(ChatGenerationSettings):
    model_id: UUID


class MessageInfo(BaseModel):
    id: UUID
    skill_snapshots: list[SkillSnapshot] = Field(default_factory=list)
    attachments: list[AttachmentSummary] = Field(default_factory=list)
    knowledge_sources: list[Hit] = Field(default_factory=list)
    role: Literal['user', 'assistant']
    content: str
    reasoning: str = ''
    run_id: UUID
    reply_to_message_id: UUID | None = None
    generation_index: int = 1
    is_complete: bool = False
    feedback: Literal['up', 'down'] | None = None
    generation_config: ChatGenerationConfig | None = None
    created_at: datetime


class MessageHistory(BaseModel):
    items: list[MessageInfo]
    next_before: tuple[datetime, UUID] | None = None


class ChatRequestContext(ChatGenerationConfig):
    """User selections only; never stores credentials or model authorization."""

    model_config = ConfigDict(extra='forbid')
    skill_slugs: list[str] = Field(default_factory=list)
    file_ids: list[UUID] = Field(default_factory=list)
    knowledge_base_ids: list[UUID] = Field(default_factory=list)


class RegenerationTarget(BaseModel):
    message_id: UUID
    question: MessageInfo
    request_context: dict[str, object]
    next_generation_index: int
