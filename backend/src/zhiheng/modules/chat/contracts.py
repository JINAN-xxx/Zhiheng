from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from zhiheng.modules.files.public import AttachmentSummary
from zhiheng.modules.knowledge.public import Hit


class ConversationInfo(BaseModel):
    id: UUID
    title: str
    version: int = 1
    archived: bool = False
    preparation_id: UUID | None = None
    created_at: datetime


class SkillSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    slug: str
    name: str
    revision_id: UUID
    content_hash: str


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
    created_at: datetime


class ChatRequestContext(BaseModel):
    """User selections only; never stores credentials or model authorization."""

    model_config = ConfigDict(extra='forbid')
    model_id: UUID
    skill_slugs: list[str] = Field(default_factory=list)
    file_ids: list[UUID] = Field(default_factory=list)
    knowledge_base_ids: list[UUID] = Field(default_factory=list)


class RegenerationTarget(BaseModel):
    message_id: UUID
    question: MessageInfo
    request_context: dict[str, object]
    next_generation_index: int
