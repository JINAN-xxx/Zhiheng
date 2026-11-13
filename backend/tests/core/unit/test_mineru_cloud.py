import json

import httpx
import pytest

from zhiheng.modules.knowledge.adapters.mineru import MinerUClient
from zhiheng.modules.knowledge.adapters.mineru_cloud import MinerUCloudClient, create_mineru_client
from zhiheng.modules.knowledge.parsing_contracts import MinerUFailure
from zhiheng.shared.errors import AppError


def transport(monkeypatch: pytest.MonkeyPatch, respond: object) -> None:
    original = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )


@pytest.mark.parametrize(
    'code,available', [(0, True), (-60012, True), ('A0202', False), ('A0211', False), (-60013, False)]
)
async def test_health_checks_auth_without_creating_tasks(
    monkeypatch: pytest.MonkeyPatch, code: object, available: bool
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={'code': code, 'data': {}})

    transport(monkeypatch, respond)
    result = await MinerUCloudClient('https://mineru.net/api/v4', 'secret').health()
    assert result.available is available
    assert len(calls) == 1 and calls[0].method == 'GET'
    assert calls[0].url.path.startswith('/api/v4/extract-results/batch/')
    assert calls[0].headers['authorization'] == 'Bearer secret'


async def test_batch_creation_preserves_signed_upload_and_checks_business_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                'code': 0,
                'data': {'batch_id': 'batch-1', 'file_urls': ['https://oss.test/upload?signature=private']},
            },
        )

    transport(monkeypatch, respond)
    client = create_mineru_client('https://mineru.net/api/v4', 'private-key')
    result = await client.create('one.pdf', 10, 'a' * 64, 'advanced')
    assert result['task_id'] == 'batch-1'
    assert result['files'][0]['upload']['url'] == 'https://oss.test/upload?signature=private'
    assert calls[0].url.path == '/api/v4/file-urls/batch'
    assert json.loads(calls[0].content) == {'files': [{'name': 'one.pdf', 'data_id': 'a' * 64}], 'model_version': 'vlm'}
    await client.upload(result['files'][0]['upload'], b'test')
    assert calls[1].method == 'PUT' and 'authorization' not in calls[1].headers
    assert 'content-type' not in calls[1].headers


@pytest.mark.parametrize(
    'state,expected',
    [
        ('waiting-file', 'waiting-file'),
        ('pending', 'pending'),
        ('converting', 'running'),
        ('running', 'running'),
        ('done', 'completed'),
        ('failed', 'failed'),
    ],
)
async def test_poll_normalizes_official_batch_states(
    monkeypatch: pytest.MonkeyPatch, state: str, expected: str
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/api/v4/extract-results/batch/batch-1'
        return httpx.Response(
            200,
            json={
                'code': 0,
                'data': {
                    'batch_id': 'batch-1',
                    'extract_result': [
                        {
                            'file_name': 'one.pdf',
                            'state': state,
                            'full_zip_url': 'https://cdn.test/result.zip',
                            'err_msg': 'secret signed URLs or private document names',
                        }
                    ],
                },
            },
        )

    transport(monkeypatch, respond)
    result = await MinerUCloudClient('https://mineru.net/api/v4', 'key').get('batch-1')
    file = result['files'][0]
    assert file['status'] == expected and file['file_id'] == 'file-0'
    assert 'secret' not in str(result)
    if state == 'done':
        assert file['result_url'] == 'https://cdn.test/result.zip'


@pytest.mark.parametrize('code', ['A0202', 'A0211', -60018])
async def test_business_error_is_not_confused_with_http_success(monkeypatch: pytest.MonkeyPatch, code: object) -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'code': code, 'msg': 'private diagnostic'})

    transport(monkeypatch, respond)
    with pytest.raises(MinerUFailure) as caught:
        await MinerUCloudClient('https://mineru.net/api/v4', 'key').create('one.pdf', 10, 'a' * 64, 'standard')
    assert caught.value.recoverable and not caught.value.unknown
    assert 'private' not in caught.value.message


async def test_uncertain_cloud_submission_is_sent_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ReadTimeout('private', request=request)

    transport(monkeypatch, respond)
    with pytest.raises(MinerUFailure) as caught:
        await MinerUCloudClient('https://mineru.net/api/v4', 'key').create('one.pdf', 10, 'a' * 64, 'standard')
    assert caught.value.unknown and len(calls) == 1


async def test_cancel_does_not_call_an_undocumented_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    transport(monkeypatch, respond)
    with pytest.raises(AppError, match='云端批次不支持取消'):
        await MinerUCloudClient('https://mineru.net/api/v4', 'key').cancel('batch-1')
    assert calls == []


def test_factory_keeps_v1_services_and_recognizes_official_v4() -> None:
    assert type(create_mineru_client('http://local.test:8000')) is MinerUClient
    assert type(create_mineru_client('https://custom.test/proxy')) is MinerUClient
    assert isinstance(create_mineru_client('https://custom.test/api/v4/'), MinerUCloudClient)
    official = create_mineru_client('https://mineru.net')
    assert isinstance(official, MinerUCloudClient) and official.base == 'https://mineru.net/api/v4'


def test_official_signed_transfers_never_receive_api_key_even_on_same_authority() -> None:
    client = MinerUCloudClient('https://mineru.net/api/v4', 'private-key')
    assert client.transfer_headers('https://mineru.net/upload?signature=test') == {}
    assert client.transfer_headers(
        'https://mineru.net/upload?signature=test',
        {'authorization': 'injected', 'x-ms-blob-type': 'BlockBlob'},
    ) == {'x-ms-blob-type': 'BlockBlob'}
