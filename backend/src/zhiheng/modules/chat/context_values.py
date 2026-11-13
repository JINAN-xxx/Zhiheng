"""Read values and commands crossing the chat memory boundary, without persistence objects."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContextValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', from_attributes=True)


class ContextStateValue(ContextValue):
    conversation_id: UUID
    owner_id: UUID
    version: int
    target_covered_count: int
    dirty: bool
    summary_task_id: UUID | None
    pending_task_id: UUID | None
    last_run_id: UUID | None
    error_code: str | None


class SummaryTaskDraft(ContextValue):
    id: UUID
    conversation_id: UUID
    owner_id: UUID
    expected_version: int
    covered_count: int
    source_hash: str
    source_refs: list[dict[str, object]]
    file_refs: list[dict[str, object]]
    previous_task_id: UUID | None = None
    retry_request_id: UUID | None = None
    model_binding: dict[str, object]


class SummaryTaskValue(SummaryTaskDraft):
    run_id: UUID | None
    status: Literal['pending', 'failed', 'published']
    summary: str
    error_code: str | None
    created_at: datetime


class ContextSnapshotDraft(ContextValue):
    conversation_id: UUID
    owner_id: UUID
    summary_task_id: UUID | None
    summary_version: int
    source_refs: list[dict[str, object]]
    file_refs: list[dict[str, object]]
    estimated_tokens: int = Field(ge=0)
    input_budget: int = Field(ge=0)
    dropped_turns: int = Field(ge=0)


class ContextSnapshotValue(ContextSnapshotDraft):
    run_id: UUID
    created_at: datetime
