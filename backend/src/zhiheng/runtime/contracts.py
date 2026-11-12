from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

RunStatus = Literal['pending', 'running', 'cancel_requested', 'completed', 'failed', 'cancelled', 'interrupted']
TERMINAL_STATUSES = frozenset({'completed', 'failed', 'cancelled', 'interrupted'})


@dataclass(frozen=True)
class ActiveRunReference:
    id: UUID
    resource_type: str
    resource_id: UUID
    status: RunStatus
    owner_id: UUID


class RunUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    status: Literal['pending', 'known', 'unknown'] = 'pending'


class RunError(BaseModel):
    code: str
    message: str


class RunSnapshot(BaseModel):
    id: UUID
    resource_type: Literal[
        'chat',
        'chat_summary',
        'connection_test',
        'public',
        'agent_preview',
        'agent_invocation',
        'work_turn',
        'knowledge_embedding',
        'knowledge_rerank',
        'knowledge_graph',
    ]
    resource_id: UUID
    status: RunStatus
    attempt_id: UUID | None
    attempt_no: int
    revision: int
    projection_seq: int
    text: str
    reasoning: str = ''
    error: RunError | None
    created_at: datetime
    usage: RunUsage = Field(default_factory=RunUsage)
    result: dict[str, object] | None = None
    finish_reason: Literal['stop', 'length'] | None = None


@dataclass(frozen=True)
class ClaimedRun:
    id: UUID
    resource_type: str
    resource_id: UUID
    owner_id: UUID
    attempt_id: UUID
    payload: dict[str, object]
    billing_owner_id: UUID | None = None
