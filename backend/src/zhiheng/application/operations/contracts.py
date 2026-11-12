"""Read projections only; execution state belongs to the original native domain."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from zhiheng.runtime.contracts import RunError

OperationStatus = Literal[
    'queued', 'running', 'succeeded', 'partially_succeeded', 'cancel_requested', 'cancelled', 'failed', 'interrupted'
]


class OperationInfo(BaseModel):
    id: UUID
    kind: Literal['knowledge_job', 'knowledge_preparation']
    native_id: UUID
    status: OperationStatus
    phase: str
    progress: dict[str, int]
    cancel_allowed: bool
    result: dict[str, object]
    error: RunError | None
    created_at: datetime
    updated_at: datetime
