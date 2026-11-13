"""Thin MinerU V1 transport. Task creation is deliberately never retried."""

import asyncio
import io
import json
import re
import zipfile
from http import HTTPStatus
from urllib.parse import quote, urljoin, urlsplit

import httpx

from zhiheng.modules.knowledge.contracts import MAX_TEXT_BYTES, ParseResult
from zhiheng.modules.knowledge.parsing_contracts import MinerUFailure, ParsingServiceStatus, endpoint
from zhiheng.shared.errors import AppError

MAX_CONTROL_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_ENTRIES = 1000
MAX_PACKAGE_BYTES = 40 * 1024 * 1024
MAX_EXPANDED_BYTES = 80 * 1024 * 1024
MAX_METADATA_BYTES = 15 * 1024 * 1024


class MinerUClient:
    def __init__(self, base_url: str, api_key: str = '') -> None:
        self.base = endpoint(base_url) or ''
        self._key = api_key

    def _headers(self) -> dict[str, str]:
        return {'Authorization': 'Bearer ' + self._key} if self._key else {}

    async def request(
        self, method: str, path: str, *, body: dict[str, object] | None = None, creating: bool = False
    ) -> dict[str, object]:
        timeout = httpx.Timeout(15, connect=3)
        payload = bytearray()
        try:
            async with (
                httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout) as client,
                client.stream(method, self.base + path, headers=self._headers(), json=body) as response,
            ):
                status = response.status_code
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_CONTROL_BYTES:
                        raise MinerUFailure(
                            'parse_submission_unknown' if creating else 'parse_service_response', unknown=creating
                        )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise MinerUFailure('parse_service_connection', recoverable=True) from None
        except httpx.HTTPError:
            raise MinerUFailure(
                'parse_submission_unknown' if creating else 'parse_service_wait', unknown=creating
            ) from None
        try:
            data = json.loads(payload)
        except (ValueError, RecursionError):
            data = None
        if status >= HTTPStatus.BAD_REQUEST:
            detail = data.get('error') if isinstance(data, dict) else None
            code = detail.get('code') if isinstance(detail, dict) else None
            tier_unavailable = code in {'quality_tier_unavailable', 'engine_unavailable', 'no_engine'}
            if isinstance(detail, dict) and code == 'invalid_request' and detail.get('param') == 'tier':
                tier_unavailable = True
            if creating and status >= HTTPStatus.INTERNAL_SERVER_ERROR and not tier_unavailable:
                raise MinerUFailure('parse_submission_unknown', unknown=True)
            recoverable = tier_unavailable or status in {401, 403, 404, 429, 502, 503, 504}
            raise MinerUFailure(
                'parse_tier_unavailable' if tier_unavailable else f'parse_service_http_{status}',
                recoverable=recoverable,
            )
        if not isinstance(data, dict):
            raise MinerUFailure('parse_submission_unknown' if creating else 'parse_service_response', unknown=creating)
        return data

    async def health(self) -> ParsingServiceStatus:
        try:
            data = await asyncio.wait_for(self.request('GET', '/v1/health'), timeout=3)
            version = data.get('version')
            runtime = data.get('runtime')
            device = runtime.get('device') if isinstance(runtime, dict) else None
            return ParsingServiceStatus(
                configured=True,
                available=data.get('status') == 'ok',
                version=version if isinstance(version, str) else None,
                device=device if isinstance(device, str) else None,
            )
        except (AppError, TimeoutError, asyncio.TimeoutError):
            return ParsingServiceStatus(configured=True, available=False)

    async def create(self, filename: str, size: int, sha256: str, tier: str) -> dict[str, object]:
        return await self.request(
            'POST',
            '/v1/tasks',
            body={
                'files': [{'file_name': filename, 'bytes': size, 'sha256sum': sha256}],
                'tier': tier,
                'output_formats': ['default'],
            },
            creating=True,
        )

    async def get(self, task_id: str) -> dict[str, object]:
        try:
            return await self.request('GET', '/v1/tasks/' + quote(task_id, safe=''))
        except MinerUFailure as error:
            if error.code == 'parse_service_http_404':
                raise AppError('parse_task_unavailable', '原解析任务已失效，原文件已保留，请重新解析', 422) from None
            raise

    async def cancel(self, task_id: str) -> None:
        await self.request('POST', '/v1/tasks/' + quote(task_id, safe='') + '/cancel')

    def transfer_url(self, value: object) -> str:
        if not isinstance(value, str):
            raise MinerUFailure('parse_service_response')
        url = urljoin(self.base + '/', value)
        try:
            checked = endpoint(url)
        except ValueError:
            # Object storage signatures legitimately use a query string. Never allow embedded credentials.
            parts = urlsplit(url)
            if (
                parts.scheme not in {'http', 'https'}
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.fragment
            ):
                raise MinerUFailure('parse_service_response') from None
            checked = url
        if not checked:
            raise MinerUFailure('parse_service_response')
        return checked

    def transfer_headers(self, url: str, supplied: object = None) -> dict[str, str]:
        # Signed upload targets may require storage-specific headers. The API's
        # credential is added separately only for the configured API authority.
        headers = (
            {
                key: value
                for key, value in supplied.items()
                if isinstance(key, str)
                and isinstance(value, str)
                and key.lower() not in {'host', 'cookie', 'proxy-authorization', 'authorization'}
            }
            if isinstance(supplied, dict)
            else {}
        )
        if urlsplit(url).netloc == urlsplit(self.base).netloc:
            headers.update(self._headers())
        return headers

    async def upload(self, target: dict[str, object], content: bytes) -> None:
        url = self.transfer_url(target.get('url'))
        try:
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=httpx.Timeout(20, connect=3)
            ) as client:
                response = await client.put(
                    url, headers=self.transfer_headers(url, target.get('headers')), content=content
                )
            if not response.is_success:
                raise MinerUFailure('parse_upload_wait')
        except httpx.HTTPError:
            raise MinerUFailure('parse_upload_wait') from None

    async def download(self, value: object) -> bytes:
        url = self.transfer_url(value)
        result = bytearray()
        try:
            async with (
                httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=httpx.Timeout(25, connect=3)
                ) as client,
                client.stream('GET', url, headers=self.transfer_headers(url)) as response,
            ):
                if not response.is_success:
                    raise MinerUFailure('parse_download_wait')
                async for part in response.aiter_bytes():
                    result.extend(part)
                    if len(result) > MAX_PACKAGE_BYTES:
                        raise MinerUFailure('parse_result_too_large')
        except httpx.HTTPError:
            raise MinerUFailure('parse_download_wait') from None
        return bytes(result)


def unpack_result(content: bytes, *, engine: str, version: str, parser_version: str, tier: str) -> ParseResult:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_PACKAGE_ENTRIES or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
                raise ValueError('Expanded result exceeds limits')
            for item in entries:
                if (
                    item.filename.startswith(('/', '\\'))
                    or '..' in item.filename.replace('\\', '/').split('/')
                    or item.flag_bits & 1
                ):
                    raise ValueError('Invalid result member')
            markdown = [item for item in entries if item.filename.lower().endswith('.md')]
            if not markdown:
                raise ValueError('No Markdown output')
            chosen = next((item for item in markdown if item.filename.rsplit('/', 1)[-1] == 'markdown.md'), markdown[0])
            if chosen.file_size > MAX_TEXT_BYTES:
                raise ValueError('Text exceeds limits')
            text = archive.read(chosen).decode('utf-8-sig')
            if not text.strip() or len(text.encode()) > MAX_TEXT_BYTES or '\x00' in text:
                raise ValueError('Empty or invalid output')
            metadata: dict[str, str | int] = {
                'engine': engine,
                'engine_version': version,
                'tier': tier,
                'format': 'markdown',
            }
            middle = next((item for item in entries if item.filename.endswith('middle_json.json')), None)
            if middle is not None and middle.file_size <= MAX_METADATA_BYTES:
                data = json.loads(archive.read(middle))
                pages = data.get('pages', data.get('pdf_info')) if isinstance(data, dict) else None
                if isinstance(pages, list):
                    metadata['pages'] = len(pages)
            # No remote asset URLs or local paths are published as readable platform files.
            text = re.sub(r'!\[([^\]]*)\]\([^\n)]*\)', r'\1', text)
            if not text.strip():
                raise ValueError('No readable text in parsing result')
            return ParseResult(text=text, metadata=metadata, parser_version=parser_version)
    except (ValueError, OSError, zipfile.BadZipFile, UnicodeError, KeyError, RecursionError):
        raise AppError('parse_result_invalid', '解析结果无效或超过限制，原文件已保留', 422) from None
