"""Values crossing the ordinary-chat checkpoint and summary boundary."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from zhiheng.modules.chat.execution import ChatExecutionSpec


class ContextValue(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', from_attributes=True)


class ContextStateValue(ContextValue):
    conversation_id: UUID
    owner_id: UUID
    version: int
    pending_task_id: UUID | None
    error_code: str | None
    agent_checkpoint_id: str | None = None
    agent_source_hash: str | None = None
    checkpoint_cleanup_after: datetime | None = None


class _SummaryTaskFields(ContextValue):
    id: UUID
    conversation_id: UUID
    owner_id: UUID
    expected_version: int
    covered_count: int
    source_hash: str
    source_refs: list[dict[str, object]]
    file_refs: list[dict[str, object]]
    model_binding: dict[str, object]


class SummaryTaskDraft(_SummaryTaskFields):
    """New summaries always belong to a specific parent execution attempt."""

    parent_run_id: UUID
    parent_attempt_id: UUID


class SummaryTaskValue(_SummaryTaskFields):
    # Completed historical rows remain readable without inventing parent facts.
    parent_run_id: UUID | None
    parent_attempt_id: UUID | None
    run_id: UUID | None
    status: Literal['pending', 'failed', 'published']
    summary: str
    error_code: str | None
    created_at: datetime


class ContextSnapshotDraft(ContextValue):
    conversation_id: UUID
    owner_id: UUID
    source_refs: list[dict[str, object]]
    file_refs: list[dict[str, object]]
    agent_context: ChatExecutionSpec | None = None


class ContextSnapshotValue(ContextSnapshotDraft):
    agent_checkpoint_id: str | None = None
    run_id: UUID
    created_at: datetime
