import pytest

from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
from zhiheng.modules.models.public import ModelMessage


@pytest.mark.asyncio
async def test_real_reasoning_stream_has_independent_delta(provider: object) -> None:
    client = OpenAIChatProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='local',
        model='reasoning',
        max_output_tokens=32,
        timeout_seconds=2,
    )
    values = [value async for value in client.stream((ModelMessage('user', 'q'),))]
    assert getattr(values[0], 'reasoning', '') == '先思考'
    assert values[0].text == ''


@pytest.mark.asyncio
async def test_provider_receives_restored_output_ceiling(provider: object) -> None:
    client = OpenAIChatProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='local',
        model='local-text',
        max_output_tokens=131072,
        timeout_seconds=2,
    )
    values = [value async for value in client.stream((ModelMessage('user', 'q'),))]
    assert ''.join(value.text for value in values) == '你好'
    assert provider.requests[0]['max_completion_tokens'] == 131072
