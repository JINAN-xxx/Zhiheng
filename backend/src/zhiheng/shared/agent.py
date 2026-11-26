"""Immutable, SDK-independent settings for one admitted conversational model call."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

SUMMARY_MESSAGE_OVERHEAD = 64
Counter = Annotated[int, Field(strict=True, ge=0)]
PositiveCounter = Annotated[int, Field(strict=True, gt=0)]
Checkpoint = Annotated[str, Field(strict=True, min_length=1)]


class AgentSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    version: Literal[1]
    reservation_input_tokens: Counter
    base_checkpoint_id: Checkpoint | None
    expected_head: Checkpoint | None
    incremental: bool = Field(strict=True)
    message_ids: tuple[StrictStr, ...]
    trigger_tokens: PositiveCounter
    keep_tokens: PositiveCounter
    summary_output_tokens: PositiveCounter
    summary_input_tokens: PositiveCounter
    input_budget: PositiveCounter | None
    reference_message_index: Counter | None = None

    @field_validator('version', mode='before')
    @classmethod
    def integer_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError('Chat execution version must be an integer')
        return value

    @field_validator('message_ids')
    @classmethod
    def unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values or any(not value or '\x00' in value for value in values) or len(set(values)) != len(values):
            raise ValueError('Chat message identities must be nonblank and unique')
        return values

    @model_validator(mode='after')
    def valid_execution(self) -> 'AgentSettings':
        if self.keep_tokens >= self.trigger_tokens:
            raise ValueError('Retained tokens must be below the summary trigger')
        if self.incremental and self.base_checkpoint_id is None:
            raise ValueError('Incremental chat requires a checkpoint')
        if self.reference_message_index is not None and self.reference_message_index >= len(self.message_ids) - 1:
            raise ValueError('Transient reference must precede the current message')
        return self
