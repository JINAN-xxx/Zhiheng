from uuid import uuid4

import pytest

from zhiheng.application.execution.executor import InferenceProgress
from zhiheng.modules.models.public import AgentModelMessage, ModelBinding, ModelDelta, ModelMessage, ModelRequest
from zhiheng.shared.text import storage_text


@pytest.mark.parametrize(
    'value,expected',
    [
        ('普通文本😀\n下一行', '普通文本😀\n下一行'),
        ('a\x00b', 'a\\u0000b'),
        ('a\ud800b', 'a\\ud800b'),
        ('literal\\u0000', 'literal\\u0000'),
    ],
)
def test_storage_text_preserves_regular_text_and_visibly_escapes_invalid_pg_text(value: str, expected: str) -> None:
    assert storage_text(value) == expected
    assert storage_text(storage_text(value)) == expected


def test_invalid_output_is_safe_before_projection_byte_count_without_changing_reported_usage() -> None:
    progress = InferenceProgress()
    progress.append(
        ModelDelta(text='结果\x00', reasoning='\ud800', input_tokens=12, output_tokens=7), agent_execution=False
    )
    assert progress.text == '结果\\u0000'
    assert progress.reasoning == '\\ud800'
    assert (progress.input_tokens, progress.output_tokens) == (12, 7)


def test_agent_wire_roundtrip_preserves_controls_without_putting_them_in_jsonb_strings() -> None:
    content = 'literal\\u0000 / raw\x00'
    request = ModelRequest(
        binding=ModelBinding(model_id=uuid4(), connection_id=uuid4(), upstream_model='test', max_output_tokens=1024),
        messages=(ModelMessage('assistant', content),),
        agent_messages=(AgentModelMessage(role='assistant', content=content),),
    )
    payload = request.model_dump(mode='json')
    assert 'agent_messages' not in payload
    assert '\x00' not in str(payload) and '\ud800' not in str(payload)
    restored = ModelRequest.model_validate(payload)
    assert restored.agent_messages[0].content == content
    assert restored.messages[0].content == storage_text(content)
    progress = InferenceProgress()
    progress.append(ModelDelta(text=content), agent_execution=False, tool_execution=True)
    assert progress.raw_text == content and progress.text == storage_text(content)
