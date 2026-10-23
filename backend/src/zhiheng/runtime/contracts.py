from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

RunStatus = Literal['pending', 'running', 'cancel_requested', 'completed', 'failed', 'cancelled', 'interrupted']
TERMINAL_STATUSES = frozenset({'completed', 'failed', 'cancelled', 'interrupted'})


class RunUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    status: Literal['pending', 'known', 'unknown'] = 'pending'


class RunError(BaseModel):
    code: str
    message: str


class RunSnapshot(BaseModel):
    id: UUID
    resource_type: Literal['chat', 'connection_test']
    resource_id: UUID
    status: RunStatus
    attempt_id: UUID | None
    attempt_no: int
    revision: int
    projection_seq: int
    text: str
    error: RunError | None
    created_at: datetime
    usage: RunUsage = Field(default_factory=RunUsage)


@dataclass(frozen=True)
class ClaimedRun:
    id: UUID
    resource_type: str
    resource_id: UUID
    owner_id: UUID
    attempt_id: UUID
    payload: dict[str, object]
