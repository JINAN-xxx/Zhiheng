import httpx2
import pytest

from tests.core.unit.test_provider_diagnostics import mock_transport
from zhiheng.modules.models.adapters.errors import provider_error
from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
from zhiheng.modules.models.public import ModelMessage


async def test_connection_and_read_timeouts_reach_the_actual_sdk_request(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = mock_transport(
        monkeypatch,
        lambda _: httpx2.Response(
            200,
            headers={'content-type': 'text/event-stream'},
            content=b'data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
        ),
    )
    provider = OpenAIChatProvider(
        base_url='https://provider.invalid/v1',
        api_key='fixture-key',
        model='fixture',
        max_output_tokens=32,
        timeout_seconds=2,
        connect_timeout_seconds=0.25,
    )
    chunks = [value async for value in provider.stream((ModelMessage('user', 'hello'),))]
    assert ''.join(value.text for value in chunks) == 'ok' and len(requests) == 1
    assert requests[0].extensions['timeout']['connect'] == 0.25
    assert requests[0].extensions['timeout']['read'] == 2


def test_connect_timeout_has_a_safe_distinct_category() -> None:
    error = provider_error(httpx2.ConnectTimeout('private-address-and-token'))
    assert error.reason == 'connect_timeout' and 'private-address-and-token' not in error.message
