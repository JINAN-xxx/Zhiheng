import pytest
from pydantic import ValidationError

from zhiheng.modules.developer.public import ApplicationCreate, ApplicationPatch, KeyCreate


def test_strict_contracts() -> None:
    assert ApplicationCreate(code='demo', name=' demo ').name == 'demo'
    assert ApplicationPatch(name=' next ', expected_version=1).name == 'next'
    with pytest.raises(ValidationError):
        KeyCreate(name='sdk', model_ids=[], monthly_budget=1.2)


def test_binding_options_are_frozen_and_detached_from_input() -> None:
    from uuid import uuid4

    from zhiheng.modules.models.public import ModelBinding

    options = {'temperature': 0.3, 'stop': ['END']}
    binding = ModelBinding(
        model_id=uuid4(), connection_id=uuid4(), upstream_model='frozen', max_output_tokens=64, options=options
    )
    options['temperature'] = 0.9
    assert binding.options.temperature == 0.3
    with pytest.raises(ValidationError):
        binding.options.temperature = 1.0
    assert binding.options.stop == ('END',)
