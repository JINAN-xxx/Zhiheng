import pytest
from pydantic import ValidationError

from tests.core.chat_spec_support import execution_spec
from zhiheng.modules.chat.execution import ChatExecutionSpec


@pytest.mark.parametrize(
    'changes',
    [
        {'version': True},
        {'version': '1'},
        {'version': 2},
        {'reservation_input_tokens': True},
        {'trigger_tokens': '1000'},
        {'chat_context_model_input_tokens': False},
        {'message_ids': ['same', 'same']},
        {'message_ids': ['']},
        {'message_ids': [1]},
        {'keep_tokens': 1000},
        {'incremental': True},
        {'incremental': 'true'},
        {'unexpected': 1},
    ],
)
def test_invalid_execution_metadata_fails_before_model_construction(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        execution_spec(**changes)


def test_execution_is_immutable_and_json_roundtrips_without_coercing_counters() -> None:
    spec = execution_spec()
    assert ChatExecutionSpec.model_validate(spec.model_dump(mode='json')) == spec
    with pytest.raises(ValidationError):
        spec.input_budget = 1
    finished = spec.completed({'summary_count': 1, 'context_model_input_tokens': 81, 'billing_cost': 999})
    assert finished.summary_used and finished.chat_context_model_input_tokens == 81
    assert not spec.summary_used and spec.chat_context_model_input_tokens is None
    assert 'billing_cost' not in finished.model_dump()


def test_regeneration_inherits_input_identity_without_old_completion_facts() -> None:
    old = execution_spec(expected_head='old-head').completed(
        {
            'summary_count': 1,
            'context_model_checkpoint_id': 'model-input',
            'context_model_reservation_tokens': 333,
            'context_model_input_tokens': 222,
            'context_image_bytes': 1000,
        }
    )
    new = old.regenerated(expected_head='current-head', input_budget=2048)
    assert new.incremental and new.base_checkpoint_id == 'model-input'
    assert new.expected_head == 'current-head' and new.reservation_input_tokens == 333
    assert new.message_ids == old.message_ids and new.summary_used
    assert new.chat_context_model_input_tokens is None and new.chat_summary_count is None
    assert new.chat_context_image_bytes is None and old.chat_context_model_input_tokens == 222
