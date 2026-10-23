from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class UsageOutcome(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    status: Literal['pending', 'known', 'unknown'] = 'pending'


class UsageRecord(BaseModel):
    id: UUID
    run_id: UUID
    model_id: UUID
    source: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    charged_tokens: int | None
    created_at: datetime


class UsageSummary(BaseModel):
    limit_tokens: int
    used_tokens: int
    reserved_tokens: int
    records: list[UsageRecord]
