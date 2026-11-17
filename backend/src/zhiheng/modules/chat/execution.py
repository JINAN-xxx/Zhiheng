"""Ordinary-chat completion facts over shared immutable Agent settings."""

from pydantic import Field

from zhiheng.shared.agent import (
    SUMMARY_MESSAGE_OVERHEAD as SUMMARY_MESSAGE_OVERHEAD,  # noqa: PLC0414 - public Chat constant remains stable
)
from zhiheng.shared.agent import AgentSettings, Checkpoint, Counter


class ChatExecutionSpec(AgentSettings):
    summary_used: bool = Field(default=False, strict=True)
    chat_summary_count: Counter | None = None
    chat_summary_estimated_tokens: Counter | None = None
    chat_context_reservation_tokens: Counter | None = None
    chat_context_image_bytes: Counter | None = None
    chat_context_model_image_bytes: Counter | None = None
    chat_context_model_checkpoint_id: Checkpoint | None = None
    chat_context_model_reservation_tokens: Counter | None = None
    chat_context_estimated_tokens: Counter | None = None
    chat_context_model_input_tokens: Counter | None = None
    chat_context_retained_rounds: Counter | None = None

    def completed(self, result: dict[str, object] | None) -> 'ChatExecutionSpec':
        values = self.model_dump()
        if result:
            # Only declared result counters can cross the adapter boundary.
            values.update(
                {
                    name: result[name.removeprefix('chat_')]
                    for name in type(self).model_fields
                    if name.startswith('chat_') and name.removeprefix('chat_') in result
                }
            )
        completed = type(self).model_validate(values)
        return completed.model_copy(
            update={'summary_used': self.summary_used or (completed.chat_summary_count or 0) > 0}
        )

    def regenerated(self, *, expected_head: str | None, input_budget: int | None) -> 'ChatExecutionSpec':
        # The new attempt inherits frozen input, never the previous attempt's completion facts.
        values = {name: value for name, value in self.model_dump().items() if not name.startswith('chat_')}
        values.update(
            expected_head=expected_head,
            input_budget=input_budget,
            base_checkpoint_id=self.chat_context_model_checkpoint_id or self.base_checkpoint_id,
            incremental=True if self.chat_context_model_checkpoint_id else self.incremental,
            reservation_input_tokens=self.chat_context_model_reservation_tokens
            if self.chat_context_model_reservation_tokens is not None
            else self.reservation_input_tokens,
        )
        return type(self).model_validate(values)
