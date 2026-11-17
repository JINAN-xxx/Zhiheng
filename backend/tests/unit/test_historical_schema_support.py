import json

import pytest

from tests.core.schema_support import assert_original_rows


def rows(value: dict[str, object]) -> list[str]:
    return [json.dumps(value)]


def test_only_explicitly_retired_columns_are_removed_from_preservation_comparison() -> None:
    original = {'user_id': 'owner', 'limit_tokens': 100, 'quota_version': 7, 'used_tokens': 42, 'reserved_tokens': 3}
    current = {'user_id': 'owner', 'used_tokens': 42, 'reserved_tokens': 3}
    assert_original_rows(rows(original), rows(current), 'developer_accounts')
    with pytest.raises(AssertionError):
        assert_original_rows(rows(original), rows({**current, 'reserved_tokens': 4}), 'developer_accounts')
    with pytest.raises(AssertionError):
        assert_original_rows(rows(original), rows(original), 'developer_accounts')
    with pytest.raises(AssertionError):
        assert_original_rows(rows(original), rows({'user_id': 'owner', 'used_tokens': 42}), 'developer_accounts')


def test_nested_retired_context_does_not_weaken_skill_or_source_facts() -> None:
    old = {
        'id': 'message',
        'knowledge_sources': [{'legacy': 'hit'}],
        'request_context': {'knowledge_base_ids': ['old'], 'skills': ['kept']},
    }
    current = {'id': 'message', 'request_context': {'skills': ['kept']}}
    assert_original_rows(rows(old), rows(current), 'chat_messages')
    with pytest.raises(AssertionError):
        assert_original_rows(rows(old), rows({'id': 'message', 'request_context': {'skills': []}}), 'chat_messages')
    old_snapshot = {'run_id': 'run', 'input_budget': 99, 'agent_context': {'input_budget': 8192}, 'file_refs': ['file']}
    current_snapshot = {'run_id': 'run', 'agent_context': {'input_budget': 8192}, 'file_refs': ['file']}
    assert_original_rows(rows(old_snapshot), rows(current_snapshot), 'chat_context_snapshots')
    with pytest.raises(AssertionError):
        assert_original_rows(
            rows(old_snapshot),
            rows({**current_snapshot, 'agent_context': {'input_budget': 1}}),
            'chat_context_snapshots',
        )
