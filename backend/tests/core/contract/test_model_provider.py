import asyncio

import pytest

from tests.core.provider import ProviderServer


async def test_real_langchain_stream_preserves_text_and_usage(provider: ProviderServer) -> None:
    from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
    from zhiheng.modules.models.public import ModelMessage

    model = OpenAIChatProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='local-test-key',
        model='local-text',
        max_output_tokens=64,
        timeout_seconds=2,
    )
    result = [chunk async for chunk in model.stream((ModelMessage('user', '你好'),))]
    assert ''.join(chunk.text for chunk in result) == '你好'
    assert result[-1].input_tokens == 8
    assert result[-1].output_tokens == 2
    assert len(provider.requests) == 1
    assert provider.requests[0]['stream'] is True


async def test_rejected_provider_request_has_public_error_and_no_hidden_retry(provider: ProviderServer) -> None:
    from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
    from zhiheng.modules.models.public import ModelMessage, ProviderError

    model = OpenAIChatProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='local-test-key',
        model='rejected',
        max_output_tokens=64,
        timeout_seconds=2,
    )
    with pytest.raises(ProviderError) as caught:
        _ = [chunk async for chunk in model.stream((ModelMessage('user', '你好'),))]
    assert 'private-provider-details' not in str(caught.value)
    assert caught.value.definitive_rejection is True
    assert len(provider.requests) == 1


async def test_same_endpoint_supports_sequential_and_concurrent_calls(provider: ProviderServer) -> None:
    from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
    from zhiheng.modules.models.public import ModelMessage

    async def invoke() -> str:
        model = OpenAIChatProvider(
            base_url=f'http://127.0.0.1:{provider.server_port}/v1',
            api_key='local-test-key',
            model='local-text',
            max_output_tokens=64,
            timeout_seconds=2,
        )
        chunks = [chunk async for chunk in model.stream((ModelMessage('user', 'hello'),))]
        return ''.join(chunk.text for chunk in chunks)

    assert await invoke() == '你好'
    assert await invoke() == '你好'
    assert await asyncio.gather(invoke(), invoke()) == ['你好', '你好']
    assert len(provider.requests) == 4


@pytest.mark.parametrize('ambient_header', ['ambient-value', '中文旧配置'])
async def test_connection_credentials_ignore_ambient_sdk_headers(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch, ambient_header: str
) -> None:
    from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
    from zhiheng.modules.models.public import ModelMessage

    monkeypatch.setenv('OPENAI_CUSTOM_HEADERS', f'Authorization: Bearer ambient-key\nX-Old-Config: {ambient_header}')
    monkeypatch.setenv('OPENAI_ORG_ID', 'ambient-org')
    monkeypatch.setenv('OPENAI_PROJECT_ID', 'ambient-project')
    model = OpenAIChatProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='persisted-key',
        model='local-text',
        max_output_tokens=64,
        timeout_seconds=2,
    )
    _ = [chunk async for chunk in model.stream((ModelMessage('user', 'hello'),))]
    headers = provider.request_headers[0]
    assert headers['authorization'] == 'Bearer persisted-key'
    assert 'x-old-config' not in headers
    assert not headers.get('openai-organization')
    assert not headers.get('openai-project')


async def test_explicit_connection_does_not_use_ambient_proxy(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
    from zhiheng.modules.models.public import ModelMessage

    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:9')
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:9')
    monkeypatch.setenv('NO_PROXY', '')
    model = OpenAIChatProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='persisted-key',
        model='local-text',
        max_output_tokens=64,
        timeout_seconds=2,
    )
    result = [chunk async for chunk in model.stream((ModelMessage('user', 'hello'),))]
    assert ''.join(chunk.text for chunk in result) == '你好'
