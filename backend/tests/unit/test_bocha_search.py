import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from zhiheng.modules.search.adapters.bocha import ENDPOINT, MAX_RESPONSE_BYTES, BochaSearch
from zhiheng.modules.search.public import WebSearchError, WebSearchRequest

KEY = 'test-search-credential'


def response(values: list[object]) -> dict[str, object]:
    return {'code': 200, 'data': {'webPages': {'value': values}}}


async def test_search_uses_fixed_endpoint_and_bounded_query_without_exposing_credentials() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=response(
                [
                    {
                        'name': '官方文档',
                        'url': 'https://example.org/docs',
                        'summary': '公开信息',
                        'siteName': '示例',
                        'datePublished': '2026-11-18',
                    },
                    {'name': '重复结果', 'url': 'https://example.org/docs'},
                    {'name': '无效链接', 'url': 'javascript:alert(1)'},
                    {'name': '含凭据链接', 'url': 'https://user:password@example.org/'},
                    {'name': '第二条', 'url': 'https://example.net/', 'snippet': '短摘要'},
                ]
            ),
        )

    provider = BochaSearch(SecretStr(KEY), transport=httpx.MockTransport(handle))
    result = await provider.search(WebSearchRequest(query='  最新公开信息  ', count=2, freshness='oneWeek'))
    assert len(requests) == 1 and str(requests[0].url) == ENDPOINT
    assert requests[0].headers['Authorization'] == 'Bearer ' + KEY
    assert json.loads(requests[0].content) == {
        'query': '最新公开信息',
        'count': 2,
        'freshness': 'oneWeek',
        'summary': True,
    }
    assert [item.url for item in result.results] == ['https://example.org/docs', 'https://example.net/']
    assert result.results[0].published_at == '2026-11-18'
    assert result.results[1].summary == '短摘要'
    assert result.search_requests == 1 and result.fetched_at.tzinfo is not None
    assert KEY not in result.model_dump_json()


@pytest.mark.parametrize('key', ['', '  '])
async def test_unconfigured_search_never_attempts_http(key: str) -> None:
    def unexpected(request: httpx.Request) -> httpx.Response:
        raise AssertionError('unconfigured provider sent a request')

    provider = BochaSearch(SecretStr(key), transport=httpx.MockTransport(unexpected))
    assert not provider.available
    with pytest.raises(WebSearchError) as caught:
        await provider.search(WebSearchRequest(query='query'))
    assert not caught.value.attempted


@pytest.mark.parametrize('status', [301, 401, 403, 429, 500])
async def test_http_failures_do_not_follow_redirects_or_leak_raw_errors(status: int) -> None:
    calls = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, headers={'location': 'https://other.example/'}, text=KEY)

    provider = BochaSearch(SecretStr(KEY), transport=httpx.MockTransport(handle))
    with pytest.raises(WebSearchError) as caught:
        await provider.search(WebSearchRequest(query='query'))
    assert len(calls) == 1
    assert caught.value.attempted and not caught.value.uncertain
    assert KEY not in caught.value.message


async def test_timeout_preserves_uncertain_request_fact() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(KEY, request=request)

    provider = BochaSearch(SecretStr(KEY), transport=httpx.MockTransport(handle))
    with pytest.raises(WebSearchError) as caught:
        await provider.search(WebSearchRequest(query='query'))
    assert caught.value.attempted and caught.value.uncertain
    assert caught.value.code == 'web_search_uncertain' and KEY not in caught.value.message


async def test_documented_quota_rejection_has_an_actionable_safe_message() -> None:
    provider = BochaSearch(
        SecretStr(KEY),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                403,
                json={
                    'code': '403',
                    'msg': 'You do not have enough money or package quota',
                    'private': KEY,
                },
            )
        ),
    )
    with pytest.raises(WebSearchError) as caught:
        await provider.search(WebSearchRequest(query='query'))
    assert caught.value.code == 'web_search_quota_exhausted'
    assert '额度不足' in caught.value.message and KEY not in caught.value.message


@pytest.mark.parametrize(
    'body',
    [
        b'not json',
        b'{"code":500,"msg":"private"}',
        b'{"code":200,"data":null}',
        b'{"code":200,"data":{"webPages":{"value":"bad"}}}',
    ],
)
async def test_invalid_responses_are_visible_failures(body: bytes) -> None:
    provider = BochaSearch(SecretStr(KEY), transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)))
    with pytest.raises(WebSearchError) as caught:
        await provider.search(WebSearchRequest(query='query'))
    assert caught.value.attempted and not caught.value.uncertain


async def test_empty_results_are_not_invented() -> None:
    provider = BochaSearch(
        SecretStr(KEY), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response([])))
    )
    result = await provider.search(WebSearchRequest(query='query'))
    assert result.results == () and result.search_requests == 1


async def test_provider_echoes_are_redacted_and_text_is_bounded() -> None:
    provider = BochaSearch(
        SecretStr(KEY),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json=response(
                    [
                        {'name': KEY + 'a' * 400, 'url': 'https://example.org/', 'summary': KEY + '\x00' + 'b' * 3000},
                        {'name': 'credential URL', 'url': 'https://example.org/?token=' + KEY},
                    ]
                ),
            )
        ),
    )
    result = await provider.search(WebSearchRequest(query='query'))
    assert len(result.results) == 1 and len(result.results[0].title) == 300
    assert len(result.results[0].summary) == 2000 and '\x00' not in result.results[0].summary
    assert KEY not in result.model_dump_json()


async def test_response_body_limit_is_enforced() -> None:
    provider = BochaSearch(
        SecretStr(KEY),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'x' * (MAX_RESPONSE_BYTES + 1))),
    )
    with pytest.raises(WebSearchError, match='搜索结果过大'):
        await provider.search(WebSearchRequest(query='query'))


@pytest.mark.parametrize(
    'changes',
    [
        {'query': ''},
        {'query': '\x00'},
        {'query': 'x' * 1001},
        {'count': True},
        {'count': 11},
        {'count': 1.5},
        {'freshness': 'anything'},
        {'url': 'http://localhost/'},
    ],
)
def test_model_cannot_supply_invalid_queries_or_override_transport(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        WebSearchRequest.model_validate({'query': 'query', **changes})
