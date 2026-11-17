from uuid import uuid4

import pytest
from pydantic import ValidationError

from zhiheng.modules.work.agent_contracts import AgentTaskConfiguration, AgentTurn, CreateAgentTask, UpdateAgentTask
from zhiheng.modules.work.public import normalize_work_fact
from zhiheng.shared.errors import AppError


def test_upload_first_draft_has_explicit_model_and_no_implicit_code_permission() -> None:
    draft = CreateAgentTask(model_id=uuid4(), client_request_id=uuid4())
    assert draft.goal == '' and draft.file_ids == [] and not draft.allow_code_execution


@pytest.mark.parametrize('value', [True, False, 0, 1.0, '1', -1, None])
def test_turn_versions_reject_non_positive_or_non_integer_values(value: object) -> None:
    with pytest.raises(ValidationError):
        AgentTurn.model_validate({'message': '分析资料', 'client_request_id': str(uuid4()), 'expected_version': value})


def test_resume_does_not_duplicate_user_message() -> None:
    assert AgentTurn(client_request_id=uuid4(), expected_version=1, resume=True).message == ''
    with pytest.raises(ValidationError):
        AgentTurn(client_request_id=uuid4(), expected_version=1, resume=True, message='重复原问题')
    with pytest.raises(ValidationError):
        AgentTurn(client_request_id=uuid4(), expected_version=1)


def test_native_skill_selection_limit_and_uniqueness_are_preserved() -> None:
    values = {'model_id': uuid4(), 'skill_slugs': [f'skill-{index}' for index in range(5)]}
    assert len(AgentTaskConfiguration.model_validate(values).skill_slugs) == 5
    with pytest.raises(ValidationError):
        AgentTaskConfiguration.model_validate({**values, 'skill_slugs': [f'skill-{index}' for index in range(6)]})
    with pytest.raises(ValidationError):
        AgentTaskConfiguration(model_id=uuid4(), skill_slugs=['same', 'same'])


@pytest.mark.parametrize('value', ['yes', 1, None])
def test_code_execution_opt_in_requires_a_real_boolean(value: object) -> None:
    with pytest.raises(ValidationError):
        AgentTaskConfiguration.model_validate({'model_id': str(uuid4()), 'allow_code_execution': value})


def test_unknown_configuration_and_null_updates_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateAgentTask.model_validate(
            {'model_id': str(uuid4()), 'client_request_id': str(uuid4()), 'api_key': 'not-accepted'}
        )
    with pytest.raises(ValidationError):
        UpdateAgentTask(expected_version=1, model_id=None)
    with pytest.raises(ValidationError):
        UpdateAgentTask(expected_version=1, title=' ')


def test_audit_normalization_is_jsonb_safe_recursive_and_does_not_mutate_execution_arguments() -> None:
    original = {'args': {'code': 'print("\\n")\x00'}, 'data': ['ok\nnext\tvalue', '\ud800', '\x1b']}
    normalized = normalize_work_fact(original)
    assert original['args'] == {'code': 'print("\\n")\x00'}
    assert normalized == {'args': {'code': 'print("\\\\n")\\u0000'}, 'data': ['ok\nnext\tvalue', '\\ud800', '\\u001b']}
    assert normalize_work_fact(normalized) == normalized
    with pytest.raises(AppError):
        normalize_work_fact({'\x00': 'first', '\\u0000': 'second'})
