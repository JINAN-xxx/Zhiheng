import json
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace

import httpx
import httpx2
import pytest

from zhiheng.modules.models.adapters import openai_chat
from zhiheng.modules.models.adapters.usage import UsageCapture
from zhiheng.modules.models.contracts import ModelMessage, ProviderError

PRIVATE_DETAIL = 'private-url-header-body-input'


def provider() -> openai_chat.OpenAIChatProvider:
    return openai_chat.OpenAIChatProvider(
        base_url='https://provider.invalid/v1',
        api_key=PRIVATE_DETAIL,
        model='local',
        max_output_tokens=32,
        timeout_seconds=2,
    )


def mock_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx2.Request], httpx2.Response]
) -> list[httpx2.Request]:
    requests: list[httpx2.Request] = []
    original = openai_chat.DefaultAsyncHttpxClient

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        openai_chat,
        'DefaultAsyncHttpxClient',
        lambda **kwargs: original(transport=httpx2.MockTransport(respond), **kwargs),
    )
    return requests


@pytest.mark.parametrize('status', [400, 429, 500, 503])
async def test_chat_http_failures_keep_safe_diagnostics_without_retry(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    requests = mock_transport(
        monkeypatch, lambda _: httpx2.Response(status, json={'error': {'message': PRIVATE_DETAIL}})
    )
    with pytest.raises(ProviderError) as caught:
        _ = [chunk async for chunk in provider().stream((ModelMessage('user', PRIVATE_DETAIL),))]
    error = caught.value
    assert error.reason == 'http_error'
    assert error.upstream_status == status
    assert error.upstream_error_type == 'APIStatusError'
    assert error.definitive_rejection is (status in {400, 429})
    assert error.code == ('provider_rejected' if status in {400, 429} else 'provider_unavailable')
    assert error.message == '模型调用失败，请稍后重试。'
    assert PRIVATE_DETAIL not in str(error) and len(requests) == 1


@pytest.mark.parametrize(
    ('failure', 'reason', 'error_type'),
    [
        (httpx2.ConnectError, 'transport_error', 'APIConnectionError'),
        (httpx2.ReadTimeout, 'timeout', 'APITimeoutError'),
    ],
)
async def test_chat_transport_failures_keep_safe_diagnostics_without_retry(
    monkeypatch: pytest.MonkeyPatch, failure: type[httpx2.RequestError], reason: str, error_type: str
) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        raise failure(PRIVATE_DETAIL, request=request)

    requests = mock_transport(monkeypatch, respond)
    with pytest.raises(ProviderError) as caught:
        _ = [chunk async for chunk in provider().stream((ModelMessage('user', PRIVATE_DETAIL),))]
    assert caught.value.reason == reason and caught.value.upstream_error_type == error_type
    assert caught.value.upstream_status is None and not caught.value.definitive_rejection
    assert PRIVATE_DETAIL not in str(caught.value) and len(requests) == 1


@pytest.mark.parametrize(
    ('wire', 'reason'),
    [
        ('data: not-json\n\n', 'invalid_response'),
        ('data: [DONE]\n\n', 'invalid_response'),
        ('data: ' + json.dumps({'error': {'message': PRIVATE_DETAIL}}) + '\n\n', 'api_error'),
        (
            'data: '
            + json.dumps({'choices': [{'index': 0, 'delta': {'content': 'partial'}}]})
            + '\n\ndata: [DONE]\n\n',
            'incomplete_stream',
        ),
        (
            'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'content_filter'}]}) + '\n\n',
            'unsupported_finish_reason',
        ),
        (
            'data: ' + json.dumps({'choices': [{'index': 0, 'delta': {'reasoning_content': 123}}]}) + '\n\n',
            'invalid_reasoning',
        ),
    ],
)
async def test_chat_protocol_failures_have_fixed_reasons(
    monkeypatch: pytest.MonkeyPatch, wire: str, reason: str
) -> None:
    requests = mock_transport(
        monkeypatch, lambda _: httpx2.Response(200, headers={'Content-Type': 'text/event-stream'}, content=wire)
    )
    with pytest.raises(ProviderError) as caught:
        _ = [chunk async for chunk in provider().stream((ModelMessage('user', 'local'),))]
    assert caught.value.reason == reason and len(requests) == 1
    assert caught.value.code == 'provider_unavailable'


async def test_invalid_converted_content_is_distinguished() -> None:
    class ConvertedStream:
        def bind(self, **_kwargs: object) -> 'ConvertedStream':
            return self

        async def astream(self, _messages: object) -> AsyncIterator[SimpleNamespace]:
            yield SimpleNamespace(content=[PRIVATE_DETAIL])

    with pytest.raises(ProviderError) as caught:
        _ = [chunk async for chunk in provider()._read(ConvertedStream(), (), UsageCapture())]
    assert caught.value.reason == 'invalid_content'


@pytest.mark.parametrize('status', [True, '500', 500.0, 99, 600, None])
def test_diagnostics_drop_untrusted_values(status: object) -> None:
    error = ProviderError(reason=PRIVATE_DETAIL, upstream_status=status, upstream_error_type=PRIVATE_DETAIL)
    assert error.reason == 'unknown'
    assert error.upstream_status is None and error.upstream_error_type is None
    assert PRIVATE_DETAIL not in repr(vars(error))


def test_exception_subclass_names_are_not_diagnostic_values() -> None:
    from zhiheng.modules.models.adapters.errors import provider_error

    private_exception = type(PRIVATE_DETAIL, (ValueError,), {})
    mapped = provider_error(private_exception(PRIVATE_DETAIL))
    assert mapped.reason == 'invalid_response' and mapped.upstream_error_type == 'ValueError'
    fallback = provider_error(RuntimeError(PRIVATE_DETAIL))
    assert fallback.reason == 'unknown' and fallback.upstream_error_type is None
    assert PRIVATE_DETAIL not in repr(vars(mapped)) and PRIVATE_DETAIL not in repr(vars(fallback))


@pytest.mark.parametrize('status', [100, 599])
def test_http_status_range_is_preserved(status: int) -> None:
    assert ProviderError(upstream_status=status).upstream_status == status


def test_invalid_options_keep_definitive_rejection() -> None:
    with pytest.raises(ProviderError) as caught:
        openai_chat.OpenAIChatProvider(
            base_url='https://provider.invalid/v1',
            api_key=PRIVATE_DETAIL,
            model='local',
            max_output_tokens=32,
            timeout_seconds=2,
            options={'response_format': PRIVATE_DETAIL},
        )
    assert caught.value.reason == 'invalid_options' and caught.value.definitive_rejection


async def test_nonstreaming_usage_capture_request_is_distinguished() -> None:
    from zhiheng.modules.models.adapters.usage import CapturingCompletions

    capture = CapturingCompletions(None, UsageCapture())
    with pytest.raises(ProviderError) as caught:
        await capture.create(stream=False)
    assert caught.value.reason == 'invalid_request'


@pytest.mark.parametrize('failure', ['http', 'timeout', 'transport', 'invalid_response'])
async def test_structured_adapter_classifies_without_exposing_provider_data(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from zhiheng.modules.models.adapters.structured import StructuredProvider

    original = httpx.AsyncClient
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout(PRIVATE_DETAIL, request=request)
        if failure == 'transport':
            raise httpx.ConnectError(PRIVATE_DETAIL, request=request)
        if failure == 'http':
            return httpx.Response(503, json={'error': PRIVATE_DETAIL})
        return httpx.Response(200, text=PRIVATE_DETAIL)

    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    model = StructuredProvider(
        base_url='https://provider.invalid/v1',
        api_key=PRIVATE_DETAIL,
        model='local',
        timeout_seconds=2,
        model_type='rerank',
        options={},
    )
    with pytest.raises(ProviderError) as caught:
        _ = [chunk async for chunk in model.stream((ModelMessage('user', 'query'), ModelMessage('user', 'document')))]
    assert (
        caught.value.reason
        == {
            'http': 'http_error',
            'timeout': 'timeout',
            'transport': 'transport_error',
            'invalid_response': 'invalid_response',
        }[failure]
    )
    assert caught.value.upstream_status == (503 if failure == 'http' else None)
    assert (
        caught.value.upstream_error_type
        == {
            'http': 'HTTPStatusError',
            'timeout': 'TimeoutException',
            'transport': 'HTTPError',
            'invalid_response': 'ValueError',
        }[failure]
    )
    assert PRIVATE_DETAIL not in str(caught.value) and len(requests) == 1
