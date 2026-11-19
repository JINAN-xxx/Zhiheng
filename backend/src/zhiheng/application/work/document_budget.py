"""Workspace input accounting shared by model admission and document reads."""

import json

from zhiheng.modules.models.public import (
    AgentModelMessage,
    ModelBinding,
    ModelMessage,
    estimate_messages,
    estimate_tool_schemas,
    input_budget_for,
)
from zhiheng.shared.text import storage_text

DEFAULT_DOCUMENT_INPUT_TOKENS = 65536
MAX_DOCUMENT_RESULT_TOKENS = 65536
TOOL_RESPONSE_HEADROOM = 1024


def model_transcript(messages: tuple[AgentModelMessage, ...]) -> tuple[ModelMessage, ...]:
    return tuple(
        ModelMessage(
            'system'
            if message.role in {'system', 'developer'}
            else 'assistant'
            if message.role == 'assistant'
            else 'user',
            storage_text(message.content)
            + (
                json.dumps([call.model_dump(mode='json') for call in message.tool_calls], ensure_ascii=True)
                if message.tool_calls
                else ''
            ),
        )
        for message in messages
    )


def context_tokens(messages: tuple[ModelMessage, ...], schemas: tuple[dict[str, object], ...]) -> int:
    return estimate_messages(messages) + estimate_tool_schemas(schemas)


def tool_result_tokens(result: dict[str, object]) -> int:
    return estimate_messages((ModelMessage('user', json.dumps(result, ensure_ascii=False)),))


def document_allowance(
    binding: ModelBinding,
    platform_input_tokens: int | None,
    messages: tuple[AgentModelMessage, ...],
    schemas: tuple[dict[str, object], ...],
) -> int:
    budget = input_budget_for(
        input_budget=platform_input_tokens,
        context_window_tokens=binding.context_window_tokens,
        max_output_tokens=binding.output_reservation_tokens,
    )
    # This fallback bounds local reads; it does not assert a vendor context capacity.
    if budget is None:
        budget = DEFAULT_DOCUMENT_INPUT_TOKENS
    return max(
        0,
        min(
            MAX_DOCUMENT_RESULT_TOKENS,
            budget - context_tokens(model_transcript(messages), schemas) - TOOL_RESPONSE_HEADROOM,
        ),
    )
