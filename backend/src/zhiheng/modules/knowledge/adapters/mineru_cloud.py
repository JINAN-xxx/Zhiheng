"""Official MinerU v4 batch uploads, normalized to the platform parsing port."""

import asyncio
from urllib.parse import quote, urlsplit

from zhiheng.modules.knowledge.adapters.mineru import MinerUClient
from zhiheng.modules.knowledge.parsing_contracts import MinerUFailure, ParsingServiceStatus
from zhiheng.shared.errors import AppError

MAX_UPSTREAM_ID = 256
FILE_ID = 'file-0'
HEALTH_BATCH = '00000000-0000-0000-0000-000000000000'
AUTH_ERRORS = frozenset({'A0202', 'A0211', '-60013'})
RETRYABLE_ERRORS = frozenset({'-10001', '-60001', '-60007', '-60009', '-60018', '-60019'})


class MinerUCloudClient(MinerUClient):
    def _data(self, payload: dict[str, object], *, creating: bool = False) -> dict[str, object]:
        code = payload.get('code')
        if code != 0 or isinstance(code, bool):
            if str(code) in AUTH_ERRORS:
                raise MinerUFailure('parse_cloud_authentication', recoverable=True)
            if str(code) == '-60012':
                raise AppError('parse_task_unavailable', '原解析任务已失效，原文件已保留，请重新解析', 422)
            if code is None:
                raise MinerUFailure(
                    'parse_submission_unknown' if creating else 'parse_service_response', unknown=creating
                )
            raise MinerUFailure('parse_cloud_rejected', recoverable=str(code) in RETRYABLE_ERRORS)
        data = payload.get('data')
        if not isinstance(data, dict):
            raise MinerUFailure('parse_submission_unknown' if creating else 'parse_service_response', unknown=creating)
        return data

    async def health(self) -> ParsingServiceStatus:
        # Official v4 has no health endpoint. A missing batch checks authentication
        # without allocating a task, uploading a file or consuming parsing pages.
        if not self._key:
            return ParsingServiceStatus(configured=True, available=False)
        try:
            payload = await asyncio.wait_for(self.request('GET', '/extract-results/batch/' + HEALTH_BATCH), timeout=3)
            code = payload.get('code')
            return ParsingServiceStatus(
                configured=True,
                available=code in (0, -60012) and not isinstance(code, bool),
                version='api-v4',
                device='cloud',
            )
        except (AppError, TimeoutError):
            return ParsingServiceStatus(configured=True, available=False)

    async def create(self, filename: str, size: int, sha256: str, tier: str) -> dict[str, object]:
        # Local quality tiers have no official v4 equivalent. The official docs
        # recommend vlm for documents; do not claim a local tier was applied.
        data = self._data(
            await self.request(
                'POST',
                '/file-urls/batch',
                body={'files': [{'name': filename, 'data_id': sha256}], 'model_version': 'vlm'},
                creating=True,
            ),
            creating=True,
        )
        batch_id, urls = data.get('batch_id'), data.get('file_urls')
        if (
            not isinstance(batch_id, str)
            or not batch_id
            or len(batch_id) > MAX_UPSTREAM_ID
            or not isinstance(urls, list)
            or len(urls) != 1
            or not isinstance(urls[0], str)
        ):
            raise MinerUFailure('parse_submission_unknown', unknown=True)
        target = self.transfer_url(urls[0])
        return {
            'task_id': batch_id,
            'files': [{'file_id': FILE_ID, 'status': 'waiting-file', 'upload': {'url': target}}],
        }

    async def get(self, task_id: str) -> dict[str, object]:
        data = self._data(await self.request('GET', '/extract-results/batch/' + quote(task_id, safe='')))
        entries = data.get('extract_result')
        if data.get('batch_id') != task_id or not isinstance(entries, list) or len(entries) != 1:
            raise MinerUFailure('parse_service_response')
        entry = entries[0]
        if not isinstance(entry, dict):
            raise MinerUFailure('parse_service_response')
        state = entry.get('state')
        states = {
            'waiting-file': 'waiting-file',
            'pending': 'pending',
            'running': 'running',
            'converting': 'running',
            'done': 'completed',
            'failed': 'failed',
        }
        if not isinstance(state, str) or state not in states:
            raise MinerUFailure('parse_service_response')
        file: dict[str, object] = {'file_id': FILE_ID, 'status': states[state]}
        if state == 'done':
            file['result_url'] = self.transfer_url(entry.get('full_zip_url'))
            file['parse'] = {'parser_version': 'unknown', 'tier': 'unknown'}
        elif state == 'failed':
            # Upstream messages can contain private filenames or signed URLs.
            file['error'] = {'code': 'parse_cloud_failed'}
        return {'task_id': task_id, 'files': [file]}

    def transfer_headers(self, url: str, supplied: object = None) -> dict[str, str]:
        # Official uploads/downloads use signed storage URLs, even when proxied
        # through the API authority. They never need the API Bearer credential.
        headers = super().transfer_headers(url, supplied)
        headers.pop('Authorization', None)
        return headers

    async def cancel(self, task_id: str) -> None:
        # The documented batch API has no cancellation endpoint. Stop local
        # observation; never invent a request or claim upstream work was stopped.
        raise AppError('parse_cloud_cancel_unavailable', '云端批次不支持取消，平台已停止等待', 409)


def create_mineru_client(base_url: str, api_key: str = '') -> MinerUClient:
    parts = urlsplit(base_url)
    path = parts.path.rstrip('/')
    if path.endswith('/api/v4'):
        return MinerUCloudClient(base_url, api_key)
    if parts.hostname == 'mineru.net' and not path:
        return MinerUCloudClient(base_url.rstrip('/') + '/api/v4', api_key)
    return MinerUClient(base_url, api_key)
