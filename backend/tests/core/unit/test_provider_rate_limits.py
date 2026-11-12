from collections.abc import Iterator

import httpx
import httpx2
import pytest
from openai import APIStatusError

from tests.core.unit.test_provider_diagnostics import mock_transport, provider
from zhiheng.modules.models.adapters.errors import provider_error
from zhiheng.modules.models.contracts import ModelMessage, ProviderError

PRIVATE_DETAIL = 'private-provider-message-header-url-credential'
GENERIC_FAILURE_MESSAGE = '模型调用失败，请稍后重试。'
METRICS = ['RPM', 'RPH', 'RPD', 'TPM', 'TPD', 'IPM', 'IPD']


def status_error(body: object, *, status: int = 429, url: str = 'https://provider.invalid/v1') -> APIStatusError:
    response = httpx2.Response(status, request=httpx2.Request('POST', url))
    return APIStatusError(PRIVATE_DETAIL, response=response, body=body)


@pytest.mark.parametrize('metric', METRICS)
@pytest.mark.parametrize('nested', [False, True])
def test_documented_rate_limit_markers_become_safe_categories(metric: str, nested: bool) -> None:
    message = f'{PRIVATE_DETAIL}. Details:{metric} limit reached.'
    body = {'error': {'message': message}} if nested else {'message': message}
    result = provider_error(status_error(body))
    assert result.rate_limit_metric == metric.lower()
    assert result.message == GENERIC_FAILURE_MESSAGE
    assert result.code == 'provider_rejected' and result.definitive_rejection
    assert result.upstream_status == 429
    assert PRIVATE_DETAIL not in result.message and PRIVATE_DETAIL not in repr(vars(result))


@pytest.mark.parametrize(
    'body',
    [
        None,
        PRIVATE_DETAIL,
        {'message': PRIVATE_DETAIL},
        {'message': {'Details': 'TPM limit reached'}},
        {'message': 'TPM'},
        {'message': 'Details:TPM limit reached eventually'},
        {'message': 'Details:TPM limit reached. Details:RPM limit reached.'},
        {'message': 'Details:TPM limit reached.', 'error': {'message': 'Details:RPM limit reached.'}},
    ],
)
def test_unknown_or_ambiguous_body_keeps_generic_limit_message(body: object) -> None:
    result = provider_error(status_error(body))
    assert result.rate_limit_metric == 'unknown'
    assert result.message == GENERIC_FAILURE_MESSAGE
    assert PRIVATE_DETAIL not in repr(vars(result))


def test_rate_limit_category_cannot_be_injected_or_used_for_other_statuses() -> None:
    result = ProviderError(upstream_status=429, rate_limit_metric=PRIVATE_DETAIL)
    assert result.rate_limit_metric == 'unknown' and result.message == GENERIC_FAILURE_MESSAGE
    other = provider_error(status_error({'message': 'Details:TPM limit reached.'}, status=503))
    assert other.rate_limit_metric == 'unknown' and other.message == GENERIC_FAILURE_MESSAGE
    assert not other.definitive_rejection


async def test_chat_adapter_translates_nested_rate_limit_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = mock_transport(
        monkeypatch,
        lambda _: httpx2.Response(429, json={'error': {'message': PRIVATE_DETAIL + '. Details:TPM limit reached.'}}),
    )
    with pytest.raises(ProviderError) as caught:
        _ = [chunk async for chunk in provider().stream((ModelMessage('user', PRIVATE_DETAIL),))]
    assert caught.value.rate_limit_metric == 'tpm' and len(requests) == 1
    assert caught.value.code == 'provider_rejected' and caught.value.definitive_rejection
    assert PRIVATE_DETAIL not in str(caught.value)


@pytest.mark.parametrize(
    'body',
    [{'message': 'Details:RPM limit reached.'}, {'error': {'message': 'Details:RPM limit reached.'}}, PRIVATE_DETAIL],
)
def test_httpx_rate_limit_uses_only_existing_response_body(body: object) -> None:
    response = httpx.Response(429, json=body, request=httpx.Request('POST', 'https://provider.invalid/v1'))
    result = provider_error(httpx.HTTPStatusError(PRIVATE_DETAIL, request=response.request, response=response))
    assert result.rate_limit_metric == ('rpm' if isinstance(body, dict) else 'unknown')
    assert result.definitive_rejection and result.code == 'provider_rejected'
    assert PRIVATE_DETAIL not in repr(vars(result))


def test_unread_httpx_error_body_does_not_trigger_io() -> None:
    def unread_body() -> Iterator[bytes]:
        raise AssertionError('Error classification must not read the response stream')
        yield b''

    response = httpx.Response(429, content=unread_body(), request=httpx.Request('POST', 'https://provider.invalid/v1'))
    result = provider_error(httpx.HTTPStatusError(PRIVATE_DETAIL, request=response.request, response=response))
    assert result.rate_limit_metric == 'unknown' and result.message == GENERIC_FAILURE_MESSAGE


def test_deeply_nested_httpx_error_body_preserves_definitive_rejection() -> None:
    response = httpx.Response(
        429,
        content='[' * 10_000 + '0' + ']' * 10_000,
        request=httpx.Request('POST', 'https://provider.invalid/v1'),
    )
    result = provider_error(httpx.HTTPStatusError(PRIVATE_DETAIL, request=response.request, response=response))
    assert result.code == 'provider_rejected' and result.definitive_rejection
    assert result.upstream_status == 429 and result.upstream_error_type == 'HTTPStatusError'
    assert result.rate_limit_metric == 'unknown' and result.message == GENERIC_FAILURE_MESSAGE
    assert PRIVATE_DETAIL not in repr(vars(result))


@pytest.mark.parametrize('code', ['1113', 1113])
@pytest.mark.parametrize('nested', [False, True])
def test_zhipu_balance_error_is_distinct_from_rate_limiting(code: str | int, nested: bool) -> None:
    body = {'code': code, 'message': PRIVATE_DETAIL + '. Details:TPM limit reached.'}
    if nested:
        body = {'error': body}
    result = provider_error(status_error(body, url='https://open.bigmodel.cn/api/paas/v4/chat/completions'))
    assert result.reason == 'insufficient_balance'
    assert result.code == 'provider_rejected' and result.definitive_rejection
    assert result.rate_limit_metric == 'unknown' and result.upstream_status == 429
    assert result.message == GENERIC_FAILURE_MESSAGE
    assert PRIVATE_DETAIL not in repr(vars(result))


@pytest.mark.parametrize(
    'url,body',
    [
        ('https://provider.invalid/v1', {'error': {'code': '1113'}}),
        ('https://open.bigmodel.cn.attacker.invalid/v1', {'error': {'code': '1113'}}),
        ('https://open.bigmodel.cn/api/paas/v4', {'error': {'code': '1302'}}),
        ('https://open.bigmodel.cn/api/paas/v4', {'error': {'code': '1113-private'}}),
        ('https://open.bigmodel.cn/api/paas/v4', {'code': '1302', 'error': {'code': '1113'}}),
    ],
)
def test_vendor_code_is_not_guessed_on_unknown_or_conflicting_responses(url: str, body: dict) -> None:
    result = provider_error(status_error(body, url=url))
    assert result.reason == 'http_error' and result.message == GENERIC_FAILURE_MESSAGE


async def test_real_sdk_zhipu_balance_response_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider

    requests = mock_transport(
        monkeypatch, lambda _: httpx2.Response(429, json={'error': {'code': '1113', 'message': PRIVATE_DETAIL}})
    )
    model = OpenAIChatProvider(
        base_url='https://open.bigmodel.cn/api/paas/v4',
        api_key=PRIVATE_DETAIL,
        model='glm-5.3',
        max_output_tokens=32,
        timeout_seconds=2,
    )
    with pytest.raises(ProviderError) as caught:
        _ = [chunk async for chunk in model.stream((ModelMessage('user', 'Hello'),))]
    assert caught.value.reason == 'insufficient_balance'
    assert caught.value.definitive_rejection and len(requests) == 1
    assert PRIVATE_DETAIL not in str(caught.value)
