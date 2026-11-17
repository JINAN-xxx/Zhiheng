"""Large model windows and explicit instance limits resolve to safe summary budgets."""

from typing import Any

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.chat.public import SummaryBudget, SummaryPolicy


def resolve(
    policy: SummaryPolicy = SummaryPolicy(),
    *,
    budget: int | None = 995648,
    window: int | None = 1000000,
    prefix: int = 0,
    current: int = 100,
) -> SummaryBudget:
    return policy.resolve(
        input_budget=budget,
        model_window_tokens=window,
        model_max_output_tokens=16384,
        prefix_tokens=prefix,
        current_tokens=current,
    )


@pytest.mark.parametrize(
    ('window', 'budget', 'trigger', 'keep'),
    [
        (32768, 28416, 19891, 5683),
        (262144, 257792, 180454, 51558),
        (1000000, 995648, 696953, 199129),
        (1048576, 1044224, 730956, 208844),
    ],
)
def test_known_capacity_scales_beyond_the_old_16k_and_65k_limits(
    window: int, budget: int, trigger: int, keep: int
) -> None:
    result = resolve(budget=budget, window=window)
    assert (result.trigger_tokens, result.keep_tokens) == (trigger, keep)
    assert result.input_tokens == budget and result.output_tokens == 4096


def test_unknown_window_keeps_conservative_defaults_without_claiming_a_megabyte() -> None:
    result = resolve(budget=None, window=None)
    assert (result.trigger_tokens, result.keep_tokens, result.input_tokens, result.output_tokens) == (
        16000,
        5000,
        65536,
        1024,
    )


def test_explicit_platform_budget_still_limits_an_unknown_window() -> None:
    result = resolve(budget=64000, window=None)
    assert (result.trigger_tokens, result.keep_tokens, result.input_tokens) == (44800, 12800, 64000)


def test_manual_overrides_remain_effective_inside_the_real_model_capacity() -> None:
    result = resolve(SummaryPolicy(60000, 10000, 120000, 2048))
    assert (result.trigger_tokens, result.keep_tokens, result.input_tokens, result.output_tokens) == (
        60000,
        10000,
        120000,
        2048,
    )


def test_small_summary_override_also_lowers_the_trigger_before_history_accumulates() -> None:
    result = resolve(SummaryPolicy(input_tokens=50000))
    assert result.input_tokens == 50000 and result.trigger_tokens == 35000
    assert result.keep_tokens < result.trigger_tokens


def test_overrides_cannot_exceed_model_capacity_and_keep_cannot_cross_trigger() -> None:
    result = resolve(SummaryPolicy(2000000, 1000000, 2000000, 4096), budget=28416, window=32768)
    assert result.input_tokens == 28416 and result.trigger_tokens == 19891
    assert result.keep_tokens == 6630


def test_trusted_prefix_is_subtracted_and_current_question_leaves_summary_space() -> None:
    result = resolve(budget=100000, prefix=10000)
    assert (result.trigger_tokens, result.keep_tokens) == (63000, 18000)
    constrained = resolve(budget=10000, window=16384, prefix=3000, current=6500)
    assert constrained.output_tokens == 436
    assert constrained.output_tokens + 3000 + 6500 + 64 == 10000


def settings(**values: Any) -> Settings:
    return Settings(
        database_url='postgresql+asyncpg://local/test',
        redis_url='redis://localhost/0',
        credential_key=Fernet.generate_key().decode(),
        **values,
    )


@pytest.mark.parametrize('value', [None, 'auto', 'AUTO', '', ' auto '])
def test_settings_accept_automatic_or_empty_values(value: str | None) -> None:
    result = settings(**{f'chat_summary_{part}_tokens': value for part in ('trigger', 'keep', 'input', 'output')})
    assert all(
        getattr(result, f'chat_summary_{part}_tokens') is None for part in ('trigger', 'keep', 'input', 'output')
    )


def test_existing_integer_configuration_and_partial_overrides_remain_valid() -> None:
    result = settings(
        chat_summary_trigger_tokens='16000',
        chat_summary_keep_tokens='5000',
        chat_summary_input_tokens='65536',
        chat_summary_output_tokens='1024',
    )
    assert result.chat_summary_trigger_tokens == 16000 and result.chat_summary_keep_tokens == 5000
    assert settings(chat_summary_keep_tokens=8000).chat_summary_trigger_tokens is None
    with pytest.raises(ValidationError):
        settings(chat_summary_trigger_tokens=1000, chat_summary_keep_tokens=1000)
