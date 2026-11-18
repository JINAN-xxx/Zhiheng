"""Bounded asynchronous Bocha search over the provider's fixed HTTPS API."""

import hashlib
import json
from http import HTTPStatus

import httpx
from pydantic import SecretStr, ValidationError

from zhiheng.modules.search.contracts import WebSearchError, WebSearchRequest, WebSearchResult, WebSearchSource
from zhiheng.shared.text import storage_text
from zhiheng.shared.time import utcnow

ENDPOINT = 'https://api.bochaai.com/v1/web-search'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class BochaSearch:
    def __init__(
        self, api_key: SecretStr, *, timeout: float = 15, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._key, self._timeout, self._transport = SecretStr(api_key.get_secret_value().strip()), timeout, transport

    @property
    def available(self) -> bool:
        return bool(self._key.get_secret_value().strip())

    def _text(self, value: object, limit: int) -> str:
        text = storage_text(value) if isinstance(value, str) else ''
        secret = self._key.get_secret_value()
        return (text.replace(secret, '[redacted]') if secret else text).strip()[:limit]

    def _source(self, value: object) -> WebSearchSource | None:
        if not isinstance(value, dict):
            return None
        address = value.get('url')
        if not isinstance(address, str) or self._key.get_secret_value() in address:
            return None
        try:
            return WebSearchSource(
                id='web-' + hashlib.sha256(address.encode()).hexdigest()[:12],
                title=self._text(value.get('name'), 300),
                url=address,
                summary=self._text(value.get('summary') or value.get('snippet'), 2000),
                site_name=self._text(value.get('siteName'), 200),
                published_at=self._text(value.get('datePublished'), 100) or None,
            )
        except (ValidationError, UnicodeError):
            return None

    def _result(self, content: bytes, request: WebSearchRequest) -> WebSearchResult:
        try:
            raw: object = json.loads(content)
        except (ValueError, UnicodeError, RecursionError):
            raise WebSearchError(
                'web_search_invalid_response', '搜索服务返回了无效结果，请稍后重试', attempted=True
            ) from None
        if not isinstance(raw, dict) or type(raw.get('code')) not in {str, int} or str(raw.get('code')) != '200':
            raise WebSearchError(
                'web_search_rejected', '搜索服务拒绝请求，请联系管理员检查服务额度与配置', attempted=True
            )
        data = raw.get('data')
        if not isinstance(data, dict):
            raise WebSearchError('web_search_invalid_response', '搜索服务返回了无效结果，请稍后重试', attempted=True)
        pages = data.get('webPages')
        values: object = pages.get('value', []) if isinstance(pages, dict) else [] if pages is None else None
        if not isinstance(values, list):
            raise WebSearchError('web_search_invalid_response', '搜索服务返回了无效结果，请稍后重试', attempted=True)
        sources: dict[str, WebSearchSource] = {}
        for value in values[:100]:
            source = self._source(value)
            if source is not None and source.url not in sources:
                sources[source.url] = source
            if len(sources) >= request.count:
                break
        return WebSearchResult(query=request.query, fetched_at=utcnow(), results=tuple(sources.values()))

    async def search(self, request: WebSearchRequest) -> WebSearchResult:
        if not self.available:
            raise WebSearchError('web_search_unavailable', '联网搜索尚未配置，请联系管理员')
        headers = {'Authorization': 'Bearer ' + self._key.get_secret_value(), 'Accept': 'application/json'}
        try:
            async with (
                httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=False, trust_env=False, transport=self._transport
                ) as client,
                client.stream(
                    'POST',
                    ENDPOINT,
                    headers=headers,
                    json={
                        **request.model_dump(mode='json'),
                        'summary': True,
                    },
                ) as response,
            ):
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise WebSearchError(
                            'web_search_response_too_large', '搜索结果过大，请缩小查询范围', attempted=True
                        )
                    content.extend(chunk)
                self._check_status(response.status_code, bytes(content))
            return self._result(bytes(content), request)
        except httpx.RequestError:
            raise WebSearchError(
                'web_search_uncertain',
                '联网搜索未能完成，原调用结果待确认，请检查记录后发送新的查询要求',
                attempted=True,
                uncertain=True,
            ) from None

    @staticmethod
    def _check_status(status: int, content: bytes = b'') -> None:
        if status == HTTPStatus.OK:
            return
        if status == HTTPStatus.FORBIDDEN:
            try:
                body: object = json.loads(content)
            except (ValueError, UnicodeError, RecursionError):
                body = None
            if (
                isinstance(body, dict)
                and str(body.get('code')) == '403'
                and 'You do not have enough money or package quota' in (body.get('msg'), body.get('message'))
            ):
                raise WebSearchError(
                    'web_search_quota_exhausted', '博查搜索额度不足，请联系管理员补充服务商资源包后重试', attempted=True
                )
        if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            message = '联网搜索服务配置不可用，请联系管理员检查凭据与额度'
        elif status == HTTPStatus.TOO_MANY_REQUESTS:
            message = '联网搜索请求受限，请稍后重试或联系管理员检查额度'
        else:
            message = '联网搜索服务暂时不可用，请稍后重试'
        raise WebSearchError('web_search_rejected', message, attempted=True)
