from tests.core.provider import ProviderServer
from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
from zhiheng.modules.models.public import ModelMessage


async def test_json_object_wire_controls_and_raw_usage_are_captured(provider: ProviderServer) -> None:
    model = OpenAIChatProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='local',
        model='cached',
        max_output_tokens=64,
        timeout_seconds=2,
        options={'response_format': 'json_object', 'reasoning_effort': 'low'},
    )
    result = [chunk async for chunk in model.stream((ModelMessage('user', 'JSON'),))]
    assert result[-1].input_tokens == 8
    assert result[-1].output_tokens == 2
    assert result[-1].cache_tokens == 4
    assert provider.requests[0]['response_format'] == {'type': 'json_object'}
    assert provider.requests[0]['reasoning_effort'] == 'low'
    assert len(provider.requests) == 1
