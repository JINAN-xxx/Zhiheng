import hashlib
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from zhiheng.api.console.chat import ChatRequest
from zhiheng.application.chat.runs import ChatRunService
from zhiheng.modules.chat.contracts import ChatRequestContext
from zhiheng.modules.models.contracts import ModelBinding, ModelOptions
from zhiheng.shared.errors import AppError
from zhiheng.shared.numbers import MAX_SAFE_INTEGER


def test_generation_settings_are_accepted_and_stored_without_changing_legacy_context() -> None:
    model_id = uuid4()
    settings = {'system_prompt': '用户提示词', 'temperature': 0.25, 'max_output_tokens': 24}
    request = ChatRequest(model_id=model_id, message='hello', client_request_id=uuid4(), **settings)
    context = ChatRequestContext(model_id=model_id, **settings)
    assert all(request.model_dump()[key] == value for key, value in settings.items())
    assert all(context.model_dump()[key] == value for key, value in settings.items())
    legacy = ChatRequestContext.model_validate({'model_id': str(model_id)})
    assert legacy.system_prompt == '' and legacy.temperature is None and legacy.max_output_tokens is None


@pytest.mark.parametrize(
    'settings',
    [
        {'system_prompt': '\x00'},
        {'system_prompt': '\ud800'},
        {'system_prompt': 'a' * 32001},
        {'system_prompt': None},
        {'temperature': True},
        {'temperature': '0.5'},
        {'temperature': float('nan')},
        {'temperature': float('inf')},
        {'temperature': -0.1},
        {'temperature': 2.1},
        {'max_output_tokens': True},
        {'max_output_tokens': '12'},
        {'max_output_tokens': 1.2},
        {'max_output_tokens': 0},
        {'max_output_tokens': MAX_SAFE_INTEGER + 1},
    ],
)
def test_generation_settings_reject_invalid_values(settings: dict) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(model_id=uuid4(), message='hello', client_request_id=uuid4(), **settings)


def test_fingerprint_preserves_default_bytes_and_distinguishes_generation_settings() -> None:
    conversation_id, model_id = uuid4(), uuid4()
    fields = ['chat', str(conversation_id), str(model_id), '你好']
    expected = hashlib.sha256(json.dumps(fields, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    args = ('chat', conversation_id, model_id, '你好')
    assert ChatRunService.fingerprint(*args) == expected
    assert ChatRunService.fingerprint(*args, system_prompt='', temperature=None, max_output_tokens=None) == expected
    changed = [
        ChatRunService.fingerprint(*args, system_prompt='instruction'),
        ChatRunService.fingerprint(*args, temperature=0),
        ChatRunService.fingerprint(*args, max_output_tokens=32),
    ]
    assert expected not in changed and len(set(changed)) == 3


def test_generation_binding_preserves_other_options_and_enforces_catalog_limit() -> None:
    binding = ModelBinding(
        model_id=uuid4(),
        connection_id=uuid4(),
        upstream_model='local',
        max_output_tokens=64,
        options=ModelOptions(temperature=0.7, top_p=0.8, stop=('END',), reasoning_effort='low'),
    )
    settings = ChatRequestContext(model_id=binding.model_id, temperature=0, max_output_tokens=24)
    selected = ChatRunService.generation_binding(binding, settings)
    assert selected.max_output_tokens == 24 and selected.options.temperature == 0
    assert selected.options.top_p == 0.8 and selected.options.stop == ('END',)
    assert selected.options.reasoning_effort == 'low'
    default = ChatRunService.generation_binding(binding, ChatRequestContext(model_id=binding.model_id))
    assert default.output_limit_tokens is None
    assert default.options.provider_output_default
    assert default.output_reservation_tokens == 64
    assert not binding.options.provider_output_default
    assert 'provider_output_default' not in binding.model_dump()['options']
    large = default.model_copy(update={'max_output_tokens': 131072})
    assert large.output_reservation_tokens == 4096
    with pytest.raises(AppError) as failure:
        ChatRunService.generation_binding(binding, ChatRequestContext(model_id=binding.model_id, max_output_tokens=65))
    assert failure.value.status_code == 422
    with pytest.raises(AppError):
        ChatRunService.generation_binding(binding.model_copy(update={'model_type': 'embedding'}), settings)
