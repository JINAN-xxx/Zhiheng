"""Documented provider aliases retain actual facts and reject ambiguous counters."""

import pytest

from zhiheng.modules.models.adapters.usage import normalize_usage


@pytest.mark.parametrize(
    ('usage', 'expected'),
    [
        ({'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}, (8, 2, None, None)),
        (
            {
                'prompt_tokens': 8,
                'completion_tokens': 2,
                'prompt_cache_hit_tokens': 6,
                'prompt_cache_miss_tokens': 2,
            },
            (8, 2, 6, None),
        ),
        (
            {
                'prompt_tokens': 8,
                'completion_tokens': 2,
                'cached_tokens': 6,
                'prompt_tokens_details': {'cached_tokens': 6, 'cache_write_tokens': 2},
            },
            (8, 2, 6, None),
        ),
        (
            {
                'input_tokens': 8,
                'output_tokens': 2,
                'input_tokens_details': {'cached_tokens': 4},
                'output_tokens_details': {'reasoning_tokens': 1},
            },
            (8, 2, 4, 1),
        ),
        (
            {
                'prompt_tokens': 8,
                'input_tokens': 8,
                'completion_tokens': 2,
                'output_tokens': 2,
                'prompt_tokens_details': {'cached_tokens': 4},
                'prompt_cache_hit_tokens': 4,
                'completion_tokens_details': {'reasoning_tokens': 1},
                'total_tokens': 10,
            },
            (8, 2, 4, 1),
        ),
        ({'prompt_tokens': 0, 'completion_tokens': 0, 'cached_tokens': 0, 'total_tokens': 0}, (0, 0, 0, None)),
        ({}, (None, None, None, None)),
        ({'prompt_tokens': 8, 'total_tokens': 10}, (8, None, None, None)),
        ({'completion_tokens': 2, 'total_tokens': 10}, (None, 2, None, None)),
        ({'total_tokens': 10}, (None, None, None, None)),
        (
            {'prompt_tokens': 8, 'completion_tokens': 2, 'prompt_tokens_details': {'cache_write_tokens': 6}},
            (8, 2, None, None),
        ),
    ],
    ids=[
        'standard-compatible',
        'deepseek-cache',
        'kimi-cache-aliases',
        'custom-counter-aliases',
        'duplicate-aliases-are-not-added',
        'confirmed-zero',
        'absent',
        'missing-output',
        'missing-input',
        'total-alone-is-not-evidence',
        'cache-write-is-not-cache-hit',
    ],
)
def test_documented_usage_shapes(usage: dict[str, object], expected: tuple[int | None, ...]) -> None:
    result = normalize_usage(usage)
    assert (result.input_tokens, result.output_tokens, result.cache_tokens, result.reasoning_tokens) == expected


@pytest.mark.parametrize('counter', [False, True, '0', -1, 0.0, 0.5, None])
@pytest.mark.parametrize('field', ['prompt_tokens', 'completion_tokens', 'cached_tokens'])
def test_invalid_raw_types_never_become_confirmed_zero(field: str, counter: object) -> None:
    result = normalize_usage({'prompt_tokens': 8, 'completion_tokens': 2, field: counter})
    if field == 'completion_tokens':
        assert result.output_tokens is None
    else:
        assert result.input_tokens is None


@pytest.mark.parametrize(
    'extra',
    [
        {'input_tokens': 9},
        {'input_tokens': '8'},
        {'input_tokens': None},
        {'cached_tokens': 9},
        {'prompt_tokens_details': {'cached_tokens': 6}, 'cached_tokens': 4},
        {'prompt_tokens_details': 'invalid'},
        {'prompt_tokens_details': {'cached_tokens': None}},
        {'prompt_cache_hit_tokens': 6, 'prompt_cache_miss_tokens': 3},
        {'prompt_cache_miss_tokens': 9},
    ],
)
def test_conflicting_or_impossible_input_facts_remain_unknown(extra: dict[str, object]) -> None:
    result = normalize_usage({'prompt_tokens': 8, 'completion_tokens': 2, **extra})
    assert result.input_tokens is None
    assert result.output_tokens == 2


@pytest.mark.parametrize('extra', [{'output_tokens': 3}, {'output_tokens': '2'}])
def test_conflicting_output_aliases_remain_unknown(extra: dict[str, object]) -> None:
    result = normalize_usage({'prompt_tokens': 8, 'completion_tokens': 2, **extra})
    assert result.input_tokens == 8
    assert result.output_tokens is None


@pytest.mark.parametrize('total', [9, 11, False, '10', -1])
def test_inconsistent_totals_do_not_settle_as_complete_usage(total: object) -> None:
    result = normalize_usage({'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': total})
    assert result.input_tokens is result.output_tokens is None


def test_rerank_sources_complete_each_other_without_overwriting_conflicts() -> None:
    result = normalize_usage({'prompt_tokens': 8}, {'input_tokens': 8, 'output_tokens': 0})
    assert (result.input_tokens, result.output_tokens) == (8, 0)
    conflict = normalize_usage({'prompt_tokens': 8}, {'input_tokens': 9, 'output_tokens': 0})
    assert (conflict.input_tokens, conflict.output_tokens) == (None, 0)
