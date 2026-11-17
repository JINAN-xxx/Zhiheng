"""Behavior contract for conversation memory, independent from database and providers."""

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from zhiheng.modules.models.public import ModelImage, ModelMessage, estimate_messages, input_budget_for

if TYPE_CHECKING:
    from zhiheng.modules.chat.context_policy import ContextTurn


def policy() -> ModuleType:
    return import_module('zhiheng.modules.chat.context_policy')


def turns(count: int) -> tuple['ContextTurn', ...]:
    module = policy()
    return tuple(
        module.ContextTurn(
            question_id=UUID(int=index * 2 + 1),
            reply_id=UUID(int=index * 2 + 2),
            question=ModelMessage('user', f'问题 {index + 1}'),
            reply=ModelMessage('assistant', f'回答 {index + 1}'),
        )
        for index in range(count)
    )


def test_chinese_estimate_is_conservative_and_images_have_fixed_token_cost() -> None:
    assert estimate_messages((ModelMessage('user', '你' * 100),)) >= 100
    plain = estimate_messages((ModelMessage('user', '描述'),))
    visual = estimate_messages((ModelMessage('user', '描述', (ModelImage('data:image/jpeg;base64,eA=='),)),))
    assert visual - plain == 2048


def test_source_fingerprint_changes_with_latest_successful_reply_version() -> None:
    module = policy()
    history = turns(1)
    revised = module.ContextTurn(
        question_id=history[0].question_id,
        reply_id=uuid4(),
        question=history[0].question,
        reply=ModelMessage('assistant', '新版本'),
        generation_index=2,
    )
    assert module.source_fingerprint(history) != module.source_fingerprint((revised,))


def test_known_window_reserves_output_and_margin() -> None:
    assert input_budget_for(context_window_tokens=300, max_output_tokens=200, safety_margin=32) == 68


def test_unknown_window_is_not_invented() -> None:
    assert input_budget_for(max_output_tokens=32768) is None


def test_explicit_input_cap_and_window_use_the_smaller_budget() -> None:
    assert input_budget_for(input_budget=4096, context_window_tokens=32768, max_output_tokens=4096) == 4096
