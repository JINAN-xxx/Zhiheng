from uuid import uuid4

import pytest

from zhiheng.modules.knowledge.retrieval_payload import validate_pending_payload, version_dependencies
from zhiheng.modules.models.public import ModelBinding, ModelRequest
from zhiheng.shared.errors import AppError


def payload(kind: str) -> tuple[dict[str, object], dict[str, object]]:
    binding = ModelBinding(model_id=uuid4(), connection_id=uuid4(), upstream_model='local-test', max_output_tokens=64)
    client_request_id = str(uuid4())
    request: dict[str, object] = {'query': 'Find the answer', 'top_k': 5}
    dependencies: dict[str, object] = {
        'bases': [{}],
        'rerank_binding': binding.model_copy(update={'model_type': 'rerank'}).model_dump(mode='json'),
        'rerank_timeout_seconds': 20,
        'embedding_timeout_seconds': 30,
    }
    if kind == 'knowledge_query':
        return request, dependencies
    dependencies['main_request'] = ModelRequest(binding=binding, messages=()).model_dump(mode='json')
    if kind == 'work_turn':
        body = {
            'agent_id': str(uuid4()),
            'client_request_id': client_request_id,
            'message': 'work',
            'expected_version': 3,
        }
        request.update(body=body, admission_body={**body, 'expected_version': 4})
        dependencies.update(
            task_version=4, member_config={}, member_skills=[], agent_version=2, agent_config=None, context_hash='hash'
        )
    else:
        request.update(runtime_request_id=client_request_id, runtime_fingerprint='fingerprint')
        if kind == 'agent_chat':
            request.update(message='hello', attachment_inputs=[])
            dependencies.update(agent_id=str(uuid4()), configuration='hash')
        else:
            request.update(
                body={'client_request_id': client_request_id, 'messages': [{'role': 'user', 'content': 'hello'}]}
            )
            dependencies.update(
                agent_version=2, config={'name': 'Assistant', 'model_id': str(binding.model_id)}, skills_hash='hash'
            )
    return request, dependencies


@pytest.mark.parametrize('kind', ['knowledge_query', 'agent_chat', 'agent_preview', 'agent_invocation', 'work_turn'])
def test_new_payload_validates_every_origin_without_rewriting_its_fields(kind: str) -> None:
    request, dependencies = payload(kind)
    versioned = version_dependencies(kind, request, dependencies)
    assert versioned == {**dependencies, 'payload_version': 1}
    validate_pending_payload(kind, request, versioned)


@pytest.mark.parametrize(
    ('kind', 'field'), [('agent_chat', 'agent_id'), ('agent_preview', 'skills_hash'), ('work_turn', 'task_version')]
)
def test_admission_and_recovery_reject_missing_frozen_dependencies(kind: str, field: str) -> None:
    request, dependencies = payload(kind)
    del dependencies[field]
    with pytest.raises(AppError) as admission:
        version_dependencies(kind, request, dependencies)
    assert admission.value.code == 'knowledge_payload_invalid'
    with pytest.raises(AppError) as recovery:
        validate_pending_payload(kind, request, {**dependencies, 'payload_version': 1})
    assert recovery.value.code == 'knowledge_payload_invalid'


def test_old_payloads_still_use_their_original_representation() -> None:
    validate_pending_payload('work_turn', {'query': 'older work'}, {'task_version': 1})
    validate_pending_payload('chat', {'query': 'older chat'}, {})
    with pytest.raises(AppError) as error:
        version_dependencies('chat', {'query': 'new chat'}, {})
    assert error.value.code == 'knowledge_payload_origin'


@pytest.mark.parametrize('version', [0, 2, True, '1'])
def test_unknown_payload_versions_fail_closed(version: object) -> None:
    request, dependencies = payload('knowledge_query')
    with pytest.raises(AppError) as error:
        validate_pending_payload('knowledge_query', request, {**dependencies, 'payload_version': version})
    assert error.value.code == 'knowledge_payload_version'


def test_unknown_origin_fails_closed_even_for_unversioned_records() -> None:
    with pytest.raises(AppError) as error:
        validate_pending_payload('other', {}, {})
    assert error.value.code == 'knowledge_payload_origin'
