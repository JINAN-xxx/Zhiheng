from uuid import uuid4

import pytest
from pydantic import ValidationError


def test_vision_binding_backward_default_and_strict() -> None:
    from zhiheng.modules.models.public import ModelBinding

    body = {'model_id': uuid4(), 'connection_id': uuid4(), 'upstream_model': 'text', 'max_output_tokens': 10}
    assert ModelBinding(**body).vision_enabled is False
    with pytest.raises(ValidationError):
        ModelBinding(**body, vision_enabled='true')
