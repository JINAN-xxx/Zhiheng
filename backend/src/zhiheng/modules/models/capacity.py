"""Configured capacity is a conservative limit, never a claim about the upstream."""

from __future__ import annotations

import json
from math import ceil
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from zhiheng.modules.models.contracts import ModelMessage

from pydantic import BaseModel, ConfigDict

SAFETY_MARGIN = 256


def input_budget_for(
    *,
    input_budget: int | None = None,
    context_window_tokens: int | None = None,
    max_output_tokens: int | None = None,
    safety_margin: int = SAFETY_MARGIN,
) -> int | None:
    if context_window_tokens is None:
        return max(0, input_budget) if input_budget is not None else None
    available = max(0, context_window_tokens - (max_output_tokens or 0) - safety_margin)
    return min(input_budget, available) if input_budget is not None else available


class ChatInputBudget(BaseModel):
    model_config = ConfigDict(frozen=True)
    limit_tokens: int | None
    source: Literal['model_window', 'platform', 'model_window_and_platform', 'unknown']
    reserved_output_tokens: int
    safety_margin_tokens: int = SAFETY_MARGIN


def chat_input_budget(
    *, context_window_tokens: int | None, max_output_tokens: int, platform_input_tokens: int | None
) -> ChatInputBudget:
    # A default chat uses the provider output default. This reservation matches
    # ModelBinding.output_reservation_tokens; an explicit output changes the budget.
    reserved = min(4096, max_output_tokens)
    source: Literal['model_window', 'platform', 'model_window_and_platform', 'unknown'] = (
        'model_window_and_platform'
        if context_window_tokens is not None and platform_input_tokens is not None
        else 'model_window'
        if context_window_tokens is not None
        else 'platform'
        if platform_input_tokens is not None
        else 'unknown'
    )
    return ChatInputBudget(
        limit_tokens=input_budget_for(
            input_budget=platform_input_tokens, context_window_tokens=context_window_tokens, max_output_tokens=reserved
        ),
        source=source,
        reserved_output_tokens=reserved,
    )


CapacityStatus = Literal['unknown', 'unverified', 'documented', 'not_applicable']


def capacity_evidence_status(model_type: str, window_tokens: int | None, note: str) -> CapacityStatus:
    if model_type != 'chat':
        return 'not_applicable'
    if window_tokens is None:
        return 'unknown'
    return 'documented' if note.strip() else 'unverified'


ASCII_LIMIT = 128


def _text_tokens(text: str) -> int:
    ascii_count = sum(ord(char) < ASCII_LIMIT for char in text)
    return ceil(ascii_count / 2) + (len(text) - ascii_count) * 2


def estimate_tool_schemas(schemas: Sequence[dict[str, object]]) -> int:
    return 16 + _text_tokens(json.dumps(schemas, ensure_ascii=False)) if schemas else 0


def estimate_messages(messages: Sequence[ModelMessage]) -> int:
    """Allow two tokens per non-ASCII character and fixed managed image costs."""
    total = 0
    for message in messages:
        total += 16 + _text_tokens(message.content)
        total += sum(image.estimated_tokens for image in message.images)
    return total


def context_image_bytes(messages: Sequence[ModelMessage]) -> int:
    """Managed image wire bytes, including retained checkpoint input."""
    return sum(len(image.data_url.encode()) for message in messages for image in message.images)
