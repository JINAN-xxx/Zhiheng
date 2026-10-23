from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationInfo(BaseModel):
    id: UUID
    title: str
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
    role: Literal['user', 'assistant']
    content: str
    reasoning: str = ''
    run_id: UUID
    created_at: datetime
