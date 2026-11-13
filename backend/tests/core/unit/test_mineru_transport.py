import io
import json
import zipfile

import httpx
import pytest

from zhiheng.modules.knowledge.adapters.mineru import MinerUClient, MinerUFailure, unpack_result
from zhiheng.modules.knowledge.parsing_contracts import ParsingSettingsPatch
from zhiheng.shared.errors import AppError


def result_package(text: str = '# Parsed document\nExample OCR text.') -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('result/markdown.md', text)
        archive.writestr('result/middle_json.json', json.dumps({'pages': [{'page_idx': 0}]}))
    return output.getvalue()


@pytest.mark.parametrize('failure', ['read', 'connect'])
async def test_create_is_sent_once_and_unknown_submission_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    original = httpx.AsyncClient
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        error = httpx.ReadTimeout if failure == 'read' else httpx.ConnectError
        raise error('private-server-detail', request=request)

    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    with pytest.raises(MinerUFailure) as caught:
        await MinerUClient('http://mineru.test', 'private-key').create('a.pdf', 10, 'a' * 64, 'standard')
    assert len(requests) == 1
    assert caught.value.unknown is (failure == 'read')
    assert caught.value.recoverable is (failure == 'connect')
    assert 'private' not in caught.value.message


async def test_v1_create_manifest_and_proxy_prefix_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    original = httpx.AsyncClient
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(202, json={'task_id': 'task-1', 'files': [{'file_id': 'file-1'}]})

    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    await MinerUClient('https://cloud.test/api', 'only-this-service').create('document.pdf', 7, 'a' * 64, 'basic')
    assert str(requests[0].url) == 'https://cloud.test/api/v1/tasks'
    assert requests[0].headers['authorization'] == 'Bearer only-this-service'
    assert json.loads(requests[0].content) == {
        'files': [{'file_name': 'document.pdf', 'bytes': 7, 'sha256sum': 'a' * 64}],
        'tier': 'basic',
        'output_formats': ['default'],
    }


def test_credentials_are_not_forwarded_to_signed_object_storage() -> None:
    client = MinerUClient('https://cloud.test/api', 'service-secret')
    url = client.transfer_url('https://storage.test/object?signature=abc')
    assert client.transfer_headers(url) == {}
    assert client.transfer_headers('https://cloud.test/api/file')['Authorization'] == 'Bearer service-secret'
    assert client.transfer_headers(url, {'x-ms-blob-type': 'BlockBlob', 'Host': 'bad', 'Cookie': 'private'}) == {
        'x-ms-blob-type': 'BlockBlob'
    }


def test_result_has_actual_engine_identity_and_omits_remote_image_paths() -> None:
    result = unpack_result(
        result_package('# Guide\n![caption](images/private.png)\nOCR text'),
        engine='mineru_local',
        version='4.2.0',
        parser_version='mineru-4.2.0-test',
        tier='standard',
    )
    assert 'OCR text' in result.text and 'private.png' not in result.text
    assert result.metadata['engine'] == 'mineru_local' and result.metadata['pages'] == 1
    assert result.parser_version == 'mineru-4.2.0-test'


@pytest.mark.parametrize('kind', ['traversal', 'empty', 'not-zip', 'no-markdown'])
def test_invalid_or_incomplete_result_is_not_published(kind: str) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr(
            '../private.md' if kind == 'traversal' else ('other.txt' if kind == 'no-markdown' else 'markdown.md'),
            '' if kind == 'empty' else 'text',
        )
    content = b'not-zip' if kind == 'not-zip' else output.getvalue()
    with pytest.raises(AppError) as caught:
        unpack_result(content, engine='mineru_cloud', version='4', parser_version='test', tier='standard')
    assert caught.value.code == 'parse_result_invalid'


@pytest.mark.parametrize('url', ['https://user:secret@host/api', 'https://host/api?key=private', 'file:///etc/passwd'])
def test_service_configuration_rejects_embedded_credentials_and_non_http(url: str) -> None:
    with pytest.raises(ValueError):
        ParsingSettingsPatch(expected_version=1, local_url=url)


async def test_cancel_uses_saved_task_id_without_creating_a_task(monkeypatch: pytest.MonkeyPatch) -> None:
    original = httpx.AsyncClient
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={'task_id': 'task-a', 'status': 'canceled'})

    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    await MinerUClient('http://local.test').cancel('task-a')
    assert len(requests) == 1 and requests[0].url.path == '/v1/tasks/task-a/cancel'


async def test_lost_upstream_task_fails_without_recreation(monkeypatch: pytest.MonkeyPatch) -> None:
    original = httpx.AsyncClient
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={'error': {'code': 'task_not_found', 'message': 'private-provider-details'}})

    monkeypatch.setattr(
        httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    with pytest.raises(AppError) as caught:
        await MinerUClient('http://local.test').get('lost-task')
    assert caught.value.code == 'parse_task_unavailable'
    assert len(requests) == 1 and requests[0].method == 'GET'
    assert 'private' not in caught.value.message


async def test_native_fallback_refuses_mixed_text_and_image_only_pages() -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    from zhiheng.modules.knowledge.adapters.native import NativeParser

    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    font = DictionaryObject(
        {
            NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/Helvetica'),
        }
    )
    page[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})}
    )
    text = DecodedStreamObject()
    text.set_data(b'BT /F1 12 Tf 10 150 Td (Readable first page) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(text)
    scanned = writer.add_blank_page(200, 200)
    picture = DecodedStreamObject()
    picture.set_data(b'\xff\xff\xff' * 4)
    picture.update(
        {
            NameObject('/Type'): NameObject('/XObject'),
            NameObject('/Subtype'): NameObject('/Image'),
            NameObject('/Width'): NumberObject(2),
            NameObject('/Height'): NumberObject(2),
            NameObject('/BitsPerComponent'): NumberObject(8),
            NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
        }
    )
    scanned[NameObject('/Resources')] = DictionaryObject(
        {NameObject('/XObject'): DictionaryObject({NameObject('/Im1'): writer._add_object(picture)})}
    )
    draw = DecodedStreamObject()
    draw.set_data(b'q 100 0 0 100 0 0 cm /Im1 Do Q')
    scanned[NameObject('/Contents')] = writer._add_object(draw)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(AppError) as caught:
        await NativeParser().parse(output.getvalue(), filename='mixed.pdf')
    assert caught.value.code == 'ocr_required'


def test_image_only_markdown_is_not_indexed_as_a_placeholder() -> None:
    with pytest.raises(AppError):
        unpack_result(
            result_package('![](images/page.jpg)'),
            engine='mineru_local',
            version='4.2.0',
            parser_version='test',
            tier='basic',
        )
