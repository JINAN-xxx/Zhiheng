from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FeedbackValue = Literal['up', 'down']


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: FeedbackValue | None


class FeedbackInfo(BaseModel):
    message_id: UUID
    value: FeedbackValue | None


class SharePreview(BaseModel):
    message_id: UUID
    question: str | None
    answer: str
    content_hash: str


class ShareCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    include_question: bool = Field(default=True, strict=True)
    expires_in_days: int = Field(default=7, ge=1, le=30, strict=True)
    expected_content_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class ShareInfo(BaseModel):
    id: UUID
    message_id: UUID
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class CreatedShareInfo(ShareInfo):
    token: str


class PublicSharedMessage(BaseModel):
    question: str | None
    answer: str
    created_at: datetime
    expires_at: datetime
