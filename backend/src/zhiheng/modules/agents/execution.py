"""Frozen assistant execution input and accepted memory."""

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from zhiheng.modules.files.public import AttachmentSummary
from zhiheng.shared.agent import AgentSettings, Checkpoint, Counter


class AgentCompletion(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    checkpoint_id: Checkpoint | None = None
    summary_count: Counter = 0
    summary_estimated_tokens: Counter = 0
    context_reservation_tokens: Counter = 0
    context_image_bytes: Counter = 0
    context_model_image_bytes: Counter = 0
    context_model_checkpoint_id: Checkpoint | None = None
    context_model_reservation_tokens: Counter = 0
    context_estimated_tokens: Counter = 0
    context_model_input_tokens: Counter = 0
    context_retained_rounds: Counter = 0


class AssistantExecutionSpec(AgentSettings):
    api_conversation_id: UUID | None = None
    base_run_id: UUID | None = None
    plaza_version: Annotated[int, Field(strict=True, ge=1)] | None = None
    sequence: Annotated[int, Field(strict=True, ge=0)] = 0
    history_sources: list[dict[str, object]] = Field(default_factory=list)
    history_files: list[AttachmentSummary] = Field(default_factory=list)
    summary_used: bool = Field(default=False, strict=True)
    completion: AgentCompletion | None = None

    def completed(self, result: dict[str, object]) -> 'AssistantExecutionSpec':
        completion = AgentCompletion.model_validate(result)
        return self.model_copy(
            update={
                'completion': completion,
                'summary_used': self.summary_used or completion.summary_count > 0,
            }
        )
