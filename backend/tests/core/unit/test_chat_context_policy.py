"""Behavior contract for conversation memory, independent from database and providers."""

from importlib import import_module
from types import ModuleType
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from zhiheng.modules.models.public import ModelImage, ModelMessage
from zhiheng.shared.errors import AppError

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


def test_29_complete_rounds_do_not_schedule_summary() -> None:
    assert policy().plan_summary(turns(29)) is None


def test_30_rounds_summarize_contiguous_first_15() -> None:
    history = turns(30)
    plan = policy().plan_summary(history)
    assert plan.covered_turns == history[:15]
    assert plan.new_turns == history[:15]
    assert plan.retained_turns == history[15:]


def test_45_rounds_merge_old_summary_with_next_15() -> None:
    history = turns(45)
    plan = policy().plan_summary(history, covered_count=15)
    assert plan.covered_turns == history[:30]
    assert plan.new_turns == history[15:30]
    assert plan.retained_turns == history[30:]


def test_summary_does_not_skip_middle_rounds_when_backlog_is_long() -> None:
    history = turns(83)
    plan = policy().plan_summary(history, covered_count=15)
    assert plan.covered_turns == history[:68]
    assert plan.new_turns == history[15:68]


def test_context_keeps_whole_recent_rounds_inside_token_budget() -> None:
    module = policy()
    history = turns(4)
    required = (ModelMessage('system', '遵守用户要求'),)
    current = ModelMessage('user', '当前问题')
    one_round_budget = module.estimate_messages((*required, history[-1].question, history[-1].reply, current))
    result = module.build_context(history, required=required, current=current, input_budget=one_round_budget)
    assert result.messages == (*required, history[-1].question, history[-1].reply, current)
    assert result.dropped_turns == 3
    assert result.estimated_tokens <= result.input_budget


def test_oversized_current_question_is_rejected_without_truncation() -> None:
    with pytest.raises(AppError) as error:
        policy().build_context(turns(2), required=(), current=ModelMessage('user', '中文' * 100), input_budget=30)
    assert error.value.code == 'context_input_too_long'


def test_required_system_and_images_are_in_same_budget() -> None:
    module = policy()
    current = ModelMessage('user', '图片问题', (ModelImage('data:image/jpeg;base64,eA=='),))
    with pytest.raises(AppError) as error:
        module.build_context(turns(1), required=(ModelMessage('system', '约束'),), current=current, input_budget=2048)
    assert error.value.code == 'context_input_too_long'


def test_model_window_deducts_output_and_margin_before_history() -> None:
    result = policy().build_context(
        turns(6),
        required=(),
        current=ModelMessage('user', '当前'),
        context_window_tokens=300,
        max_output_tokens=200,
        safety_margin=32,
    )
    assert result.input_budget == 68
    assert result.estimated_tokens <= 68


def test_missing_model_window_does_not_invent_an_input_limit() -> None:
    result = policy().build_context(turns(2), required=(), current=ModelMessage('user', '当前'), max_output_tokens=1024)
    assert result.input_budget is None


def test_summary_is_untrusted_user_data_and_preserves_latest_15_rounds() -> None:
    module = policy()
    history = turns(30)
    result = module.build_context(
        history, required=(), current=ModelMessage('user', '新问题'), summary='用户选择了方案A', covered_count=15
    )
    assert result.summary_used
    assert result.messages[0].role == 'user'
    assert '用户选择了方案A' in result.messages[0].content
    assert result.messages[1:-1] == tuple(message for turn in history[15:] for message in (turn.question, turn.reply))


def test_chinese_estimate_is_conservative_and_images_have_fixed_token_cost() -> None:
    module = policy()
    assert module.estimate_messages((ModelMessage('user', '你' * 100),)) >= 100
    plain = module.estimate_messages((ModelMessage('user', '描述'),))
    visual = module.estimate_messages((ModelMessage('user', '描述', (ModelImage('data:image/jpeg;base64,eA=='),)),))
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


def test_token_pressure_summarizes_before_round_threshold_and_keeps_latest_rounds() -> None:
    history = turns(10)
    plan = policy().plan_summary(history, force=True)
    assert plan.covered_turns == history[:5]
    assert plan.retained_turns == history[5:]
    assert policy().plan_summary(history[:1], force=True) is None


def test_summary_threshold_and_retention_are_configurable() -> None:
    history = turns(8)
    plan = policy().plan_summary(history, trigger_rounds=8, keep_rounds=3)
    assert plan.covered_turns == history[:5] and plan.retained_turns == history[5:]


def test_unknown_window_has_no_implicit_cap_even_for_long_outputs() -> None:
    result = policy().build_context(
        turns(2), required=(), current=ModelMessage('user', '当前'), max_output_tokens=32768
    )
    assert result.input_budget is None


def test_large_attachment_text_is_preserved_when_no_capacity_is_configured() -> None:
    content = '附件正文' * 10000
    result = policy().build_context(turns(2), required=(), current=ModelMessage('user', content))
    assert result.input_budget is None
    assert result.messages[-1].content == content


def test_an_explicit_platform_input_cap_is_still_respected() -> None:
    with pytest.raises(AppError) as error:
        policy().build_context((), required=(), current=ModelMessage('user', '中' * 5000), input_budget=8192)
    assert error.value.details == {'estimated_input_tokens': 10016, 'input_budget': 8192}


def test_partial_summary_retains_original_target_until_all_batches_publish() -> None:
    history = turns(30)
    plan = policy().plan_summary(history, covered_count=3, target_covered_count=15)
    assert plan.covered_turns == history[:15]
    assert plan.new_turns == history[3:15]
    assert plan.retained_turns == history[15:]
