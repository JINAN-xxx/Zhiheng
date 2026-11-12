import io
import json
import threading
from collections.abc import Iterator
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select, update

from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_knowledge_failures import source
from tests.core.provider import ProviderServer
from tests.core.unit.test_mineru_transport import result_package
from zhiheng.application.files.parsing import DocumentParsing
from zhiheng.modules.knowledge.adapters.mineru import MinerUClient, MinerUFailure
from zhiheng.modules.knowledge.parsing_orm import ParsingConfiguration, ParsingJob
from zhiheng.shared.time import utcnow


class LocalMinerU:
    def __init__(self) -> None:
        self.healthy = True
        self.official_v4 = False
        self.complete = True
        self.file_error: str | None = None
        self.calls: list[tuple[str, str, str | None]] = []
        self.uploads = 0
        self.creates = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                pass

            def respond(self, value: object, status: int = 200) -> None:
                body = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def record(self) -> None:
                owner.calls.append((self.command, self.path, self.headers.get('Authorization')))

            def do_GET(self) -> None:
                self.record()
                if owner.official_v4 and self.path.startswith('/api/v4/extract-results/batch/'):
                    if self.path.endswith('/00000000-0000-0000-0000-000000000000'):
                        self.respond({'code': -60012})
                        return
                    self.respond(
                        {
                            'code': 0,
                            'data': {
                                'batch_id': 'batch-a',
                                'extract_result': [
                                    {
                                        'file_name': 'document.pdf',
                                        'state': 'done'
                                        if owner.uploads and owner.complete
                                        else 'pending'
                                        if owner.uploads
                                        else 'waiting-file',
                                        'full_zip_url': owner.url + '/result',
                                    }
                                ],
                            },
                        }
                    )
                elif self.path == '/v1/health':
                    self.respond({'status': 'ok', 'version': '4.2.0'}, 200 if owner.healthy else 503)
                elif self.path == '/result':
                    self.respond(result_package())
                else:
                    state = (
                        'failed'
                        if owner.file_error
                        else 'completed'
                        if owner.uploads and owner.complete
                        else 'pending'
                        if owner.uploads
                        else 'waiting-file'
                    )
                    item = {'file_id': 'file-a', 'file_name': 'document.pdf', 'status': state}
                    if not owner.uploads:
                        item['upload'] = {'url': owner.url + '/upload', 'headers': {}}
                    if state == 'completed':
                        item['result_url'] = owner.url + '/result'
                    if state == 'failed':
                        item['error'] = {'code': owner.file_error, 'message': 'private-provider-details'}
                    self.respond({'task_id': 'task-a', 'status': state, 'files': [item]})

            def do_POST(self) -> None:
                self.record()
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                if owner.official_v4 and self.path == '/api/v4/file-urls/batch':
                    owner.creates += 1
                    self.respond(
                        {
                            'code': 0,
                            'data': {'batch_id': 'batch-a', 'file_urls': [owner.url + '/upload?signature=test']},
                        }
                    )
                elif self.path.endswith('/cancel'):
                    self.respond({'task_id': 'task-a', 'status': 'canceled', 'files': []})
                else:
                    owner.creates += 1
                    self.respond(
                        {
                            'task_id': 'task-a',
                            'status': 'waiting-file',
                            'files': [
                                {
                                    'file_id': 'file-a',
                                    'file_name': 'document.pdf',
                                    'status': 'waiting-file',
                                    'upload': {'url': owner.url + '/upload'},
                                }
                            ],
                        },
                        202,
                    )

            def do_PUT(self) -> None:
                self.record()
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                owner.uploads += 1
                self.respond({'task_id': 'task-a', 'file_id': 'file-a', 'status': 'pending'})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@pytest.fixture
def miners() -> Iterator[tuple[LocalMinerU, LocalMinerU]]:
    local, cloud = LocalMinerU(), LocalMinerU()
    try:
        yield local, cloud
    finally:
        local.close()
        cloud.close()


def pdf(*, blank: bool = False) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    if not blank:
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
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 10 150 Td (Document parsing source) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


async def configure(
    admin: httpx.AsyncClient, local: LocalMinerU, cloud: LocalMinerU, *, cloud_enabled: bool = True
) -> None:
    before = (await admin.get('/api/knowledge/settings/parsing')).json()
    response = await admin.patch(
        '/api/knowledge/settings/parsing',
        json={
            'expected_version': before['version'],
            'local_url': local.url,
            'local_api_key': 'local-only-secret',
            'cloud_url': cloud.url + ('/api/v4' if cloud.official_v4 else ''),
            'cloud_api_key': 'cloud-only-secret',
            'cloud_enabled': cloud_enabled,
        },
    )
    assert response.status_code == 200, response.text
    assert 'only-secret' not in response.text


async def begin(admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, *, blank: bool = False) -> UUID:
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Parsing', 'embed_model_id': model})).json()
    file = await source(admin, 'document.pdf', pdf(blank=blank))
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={'file_ids': [file['id']], 'client_request_id': str(uuid4())},
    )
    assert response.status_code == 202, response.text
    identifier = UUID(response.json()['id'])
    await app.state.container.knowledge_jobs.tick(identifier)
    return identifier


async def state(app: FastAPI, parent: UUID) -> dict[str, Any]:
    async with app.state.container.database.sessions() as session:
        row = await session.scalar(select(ParsingJob).where(ParsingJob.parent_id == parent))
        assert row is not None
        return {
            'id': row.id,
            'phase': row.phase,
            'task_id': row.task_id,
            'engine': row.engine,
            'artifact_id': row.artifact_id,
            'code': row.error_code,
            'config': row.config,
            'file_id': row.file_id,
        }


async def due(app: FastAPI) -> None:
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(update(ParsingJob).values(next_poll_at=utcnow() - timedelta(seconds=1)))


async def finish_parse(app: FastAPI, parent: UUID) -> dict[str, Any]:
    for _ in range(14):
        await due(app)
        await app.state.container.document_parsing.tick()
        result = await state(app, parent)
        if result['phase'] in {'completed', 'failed', 'cancelled', 'submission_unknown'}:
            return result
    raise AssertionError(await state(app, parent))


async def test_local_preferred_and_saved_task_resumes_after_coordinator_restart(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    await configure(admin, local, cloud)
    parent = await begin(admin, app, provider)
    c = app.state.container
    await c.document_parsing.tick()
    assert (await state(app, parent))['task_id'] == 'task-a'
    resumed = DocumentParsing(
        c.database,
        c.files,
        c.file_inputs,
        c.identity,
        c.parsing_settings,
        c.document_parsing.client_factory,
        c.document_parsing.validate_source,
        c.document_parsing.decode_result,
    )
    resumed.authorize_origin = c.knowledge_jobs.authorize_parse_origin
    c.document_parsing = resumed
    result = await finish_parse(app, parent)
    assert result['phase'] == 'completed' and result['engine'] == 'local'
    assert local.creates == 1 and cloud.creates == 0
    assert local.uploads == 1 and provider.requests == []
    async with c.database.sessions() as session:
        user = await c.identity.principal_for_user(session, (await session.get(ParsingJob, result['id'])).owner_id)
        raw = await c.files.artifact_content(session, user, result['artifact_id'])
        assert b'mineru_local' in raw and b'native-1' not in raw
    await c.knowledge_jobs.tick(parent)
    assert (await admin.get(f'/api/knowledge/jobs/{parent}')).json()['phase'] == 'parse'


async def test_official_cloud_fallback_resumes_signed_upload_after_restart(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    local.healthy = False
    cloud.official_v4 = True
    await configure(admin, local, cloud)
    health = (await admin.get('/api/knowledge/settings/parsing/status')).json()
    assert not health['local']['available'] and health['cloud']['available']
    parent = await begin(admin, app, provider)
    c = app.state.container
    for _ in range(4):
        await due(app)
        await c.document_parsing.tick()
        parsed = await state(app, parent)
        if parsed['phase'] == 'uploading':
            break
    assert parsed['task_id'] == 'batch-a' and cloud.creates == 1
    assert parsed['config']['upload_target']['url'].endswith('/upload?signature=test')
    resumed = DocumentParsing(
        c.database,
        c.files,
        c.file_inputs,
        c.identity,
        c.parsing_settings,
        c.document_parsing.client_factory,
        c.document_parsing.validate_source,
        c.document_parsing.decode_result,
    )
    resumed.authorize_origin = c.knowledge_jobs.authorize_parse_origin
    c.document_parsing = resumed
    result = await finish_parse(app, parent)
    assert result['phase'] == 'completed' and result['engine'] == 'cloud'
    assert 'upload_target' not in result['config']
    assert local.creates == 0 and cloud.creates == cloud.uploads == 1
    assert all(auth is None for method, _, auth in cloud.calls if method == 'PUT')


@pytest.mark.parametrize('cloud_enabled', [False, True])
async def test_local_unavailable_skips_disabled_cloud_or_uses_enabled_cloud(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    miners: tuple[LocalMinerU, LocalMinerU],
    cloud_enabled: bool,
) -> None:
    local, cloud = miners
    local.healthy = False
    await configure(admin, local, cloud, cloud_enabled=cloud_enabled)
    parent = await begin(admin, app, provider)
    result = await finish_parse(app, parent)
    assert result['phase'] == 'completed'
    assert result['engine'] == ('cloud' if cloud_enabled else 'native')
    assert local.creates == 0 and cloud.creates == int(cloud_enabled)
    assert provider.requests == []


async def test_scanned_pdf_without_ocr_does_not_become_empty_success(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    local.healthy = False
    await configure(admin, local, cloud, cloud_enabled=False)
    parent = await begin(admin, app, provider, blank=True)
    result = await finish_parse(app, parent)
    assert result['phase'] == 'failed' and result['code'] == 'ocr_required'
    assert result['artifact_id'] is None and cloud.creates == 0
    assert (await admin.get(f'/api/files/{result["file_id"]}')).status_code == 200


async def test_cancel_stops_known_upstream_task_and_never_switches_cloud(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    local.complete = False
    await configure(admin, local, cloud)
    parent = await begin(admin, app, provider)
    await app.state.container.document_parsing.tick()
    assert (await state(app, parent))['task_id'] == 'task-a'
    response = await admin.post(f'/api/knowledge/jobs/{parent}/cancel', json={})
    assert response.status_code == 200, response.text
    result = await finish_parse(app, parent)
    assert result['phase'] == 'cancelled' and result['artifact_id'] is None
    assert any(path.endswith('/cancel') for _, path, _ in local.calls)
    assert local.uploads == cloud.creates == 0


async def test_expired_creating_lease_becomes_unknown_without_resubmission(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    await configure(admin, local, cloud)
    parent = await begin(admin, app, provider)
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            update(ParsingJob)
            .where(ParsingJob.parent_id == parent)
            .values(phase='creating', lease_until=utcnow() - timedelta(seconds=1))
        )
    await app.state.container.document_parsing.tick()
    result = await state(app, parent)
    assert result['phase'] == 'submission_unknown'
    assert local.creates == cloud.creates == 0


async def test_ambiguous_creation_does_not_repeat_or_switch_provider(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    miners: tuple[LocalMinerU, LocalMinerU],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, cloud = miners
    await configure(admin, local, cloud)
    parent = await begin(admin, app, provider)
    count = 0

    async def unknown(*_args: object, **_kwargs: object) -> None:
        nonlocal count
        count += 1
        raise MinerUFailure('parse_submission_unknown', unknown=True)

    monkeypatch.setattr(MinerUClient, 'create', unknown)
    result = await finish_parse(app, parent)
    assert result['phase'] == 'submission_unknown'
    for _ in range(3):
        await app.state.container.document_parsing.tick()
    assert count == 1 and cloud.creates == 0


async def test_file_error_is_not_retried_in_cloud(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    local.file_error = 'encrypted_document'
    await configure(admin, local, cloud)
    parent = await begin(admin, app, provider)
    result = await finish_parse(app, parent)
    assert result['phase'] == 'failed' and cloud.creates == 0
    assert result['artifact_id'] is None


async def test_settings_secrets_are_encrypted_and_updates_are_versioned(
    admin: httpx.AsyncClient, app: FastAPI, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    before = (await admin.get('/api/knowledge/settings/parsing')).json()
    await configure(admin, local, cloud)
    after = (await admin.get('/api/knowledge/settings/parsing')).json()
    assert after['has_local_key'] and after['has_cloud_key']
    assert 'only-secret' not in json.dumps(after)
    async with app.state.container.database.sessions() as session:
        config = (await session.get(ParsingConfiguration, 1)).config
        assert 'only-secret' not in json.dumps(config)
    stale = await admin.patch('/api/knowledge/settings/parsing', json={'expected_version': before['version']})
    assert stale.status_code == 409


@pytest.mark.parametrize('kind', ['encrypted', 'corrupt'])
async def test_invalid_pdf_never_reaches_any_remote_parser(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU], kind: str
) -> None:
    local, cloud = miners
    await configure(admin, local, cloud)
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Validation', 'embed_model_id': model})).json()
    if kind == 'encrypted':
        writer = PdfWriter()
        writer.add_blank_page(100, 100)
        writer.encrypt('private-password')
        output = io.BytesIO()
        writer.write(output)
        content = output.getvalue()
    else:
        content = b'%PDF-1.7\ntruncated document'
    file = await source(admin, 'invalid.pdf', content)
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={'file_ids': [file['id']], 'client_request_id': str(uuid4())},
    )
    parent = UUID(response.json()['id'])
    await app.state.container.knowledge_jobs.tick(parent)
    result = await finish_parse(app, parent)
    assert result['phase'] == 'failed'
    assert local.creates == cloud.creates == local.uploads == 0


async def test_polling_failure_resumes_known_task_without_cloud_or_duplicate_create(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    miners: tuple[LocalMinerU, LocalMinerU],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local, cloud = miners
    await configure(admin, local, cloud)
    parent = await begin(admin, app, provider)
    await app.state.container.document_parsing.tick()
    original = MinerUClient.get

    async def temporary(_client: MinerUClient, _task: str) -> dict[str, object]:
        raise MinerUFailure('parse_service_wait')

    monkeypatch.setattr(MinerUClient, 'get', temporary)
    for _ in range(3):
        await due(app)
        await app.state.container.document_parsing.tick()
    assert (await state(app, parent))['task_id'] == 'task-a'
    assert local.creates == 1 and cloud.creates == 0
    monkeypatch.setattr(MinerUClient, 'get', original)
    assert (await finish_parse(app, parent))['phase'] == 'completed'
    assert local.creates == 1


async def test_preview_reuses_mineru_artifact_without_native_reparse(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    from zhiheng.modules.knowledge.orm import KnowledgeJob

    local, cloud = miners
    await configure(admin, local, cloud)
    parent = await begin(admin, app, provider, blank=True)
    result = await finish_parse(app, parent)
    assert result['phase'] == 'completed'
    c = app.state.container
    await c.knowledge_jobs.tick(parent)
    async with c.database.sessions() as session:
        job = await session.get(KnowledgeJob, parent)
        assert job is not None
        parsing_job = await session.get(ParsingJob, result['id'])
        assert parsing_job is not None
        principal = await c.identity.principal_for_user(session, parsing_job.owner_id)
        document_id = UUID(job.documents[0]['document_id'])
        base_id = job.base_id
    preview = await c.knowledge_documents.preview(principal, base_id, document_id)
    assert 'OCR text' in preview['text'] and str(preview['parser_version']).startswith('mineru-')
    documents = (await admin.get(f'/api/knowledge/bases/{base_id}/documents')).json()['items']
    assert documents[0]['parsing_engine'] == 'local'
    assert local.creates == 1 and cloud.creates == 0


async def test_changed_source_access_snapshot_cancels_before_upload(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    from zhiheng.modules.files.orm import FileRecord

    local, cloud = miners
    await configure(admin, local, cloud)
    parent = await begin(admin, app, provider)
    await app.state.container.document_parsing.tick()
    result = await state(app, parent)
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            update(FileRecord).where(FileRecord.id == result['file_id']).values(acl_version=FileRecord.acl_version + 1)
        )
    result = await finish_parse(app, parent)
    assert result['phase'] == 'failed' and result['artifact_id'] is None
    assert local.uploads == cloud.creates == 0
    assert any(path.endswith('/cancel') for _, path, _ in local.calls)


async def test_settings_require_platform_configuration_permission(
    admin: httpx.AsyncClient, app: FastAPI, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    from tests.core.integration.test_work import member_client

    async with member_client(app) as member:
        for path in ['/api/knowledge/settings/parsing', '/api/knowledge/settings/parsing/status']:
            response = await member.get(path)
            assert response.status_code == 403
        response = await member.patch(
            '/api/knowledge/settings/parsing',
            json={'expected_version': 1, 'cloud_enabled': True, 'cloud_url': 'https://not-allowed.test'},
        )
        assert response.status_code == 403


async def test_complete_index_build_uses_mineru_text_and_retains_normal_preview(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    from tests.core.integration.test_knowledge import VectorFixture
    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    local, cloud = miners
    await configure(admin, local, cloud)
    c = app.state.container
    c.knowledge_jobs.vectors = VectorFixture()
    parent = await begin(admin, app, provider, blank=True)
    for _ in range(35):
        await due(app)
        await c.document_parsing.tick()
        await c.knowledge_jobs.tick(parent)
        async with c.database.sessions() as session:
            runs = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for identifier in runs:
            await execute_run(c, identifier)
        result = (await admin.get(f'/api/knowledge/jobs/{parent}')).json()
        if result['status'] in {'completed', 'partial', 'failed', 'cancelled', 'interrupted'}:
            break
    assert result['status'] == 'completed', result
    assert result['chunk_count'] > 0
    assert local.creates == local.uploads == 1 and cloud.creates == 0


async def test_changed_endpoint_does_not_reuse_old_credentials(
    admin: httpx.AsyncClient, app: FastAPI, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    await configure(admin, local, cloud)
    current = (await admin.get('/api/knowledge/settings/parsing')).json()
    response = await admin.patch(
        '/api/knowledge/settings/parsing',
        json={
            'expected_version': current['version'],
            'local_url': cloud.url,
            'cloud_enabled': False,
        },
    )
    assert response.status_code == 200
    assert response.json()['has_local_key'] is False
    async with app.state.container.database.sessions() as session:
        config = (await session.get(ParsingConfiguration, 1)).config
        assert config['local_key'] is None


async def test_known_busy_local_service_waits_instead_of_uploading_to_cloud(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    local.complete = False
    await configure(admin, local, cloud)
    first = await begin(admin, app, provider)
    await app.state.container.document_parsing.tick()
    assert (await state(app, first))['task_id'] == 'task-a'
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            update(ParsingJob)
            .where(ParsingJob.parent_id == first)
            .values(next_poll_at=utcnow() + timedelta(seconds=60))
        )
    second = await begin(admin, app, provider)
    local.healthy = False
    await app.state.container.document_parsing.tick()
    result = await state(app, second)
    assert result['phase'] == 'selecting' and result['engine'] == 'local' and result['task_id'] is None
    assert local.creates == 1 and cloud.creates == 0


async def test_cloud_only_deployment_does_not_require_local_mineru(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    await configure(admin, local, cloud)
    current = (await admin.get('/api/knowledge/settings/parsing')).json()
    response = await admin.patch(
        '/api/knowledge/settings/parsing',
        json={
            'expected_version': current['version'],
            'local_url': None,
            'cloud_url': cloud.url + ('/api/v4' if cloud.official_v4 else ''),
            'cloud_enabled': True,
        },
    )
    assert response.status_code == 200
    parent = await begin(admin, app, provider)
    result = await finish_parse(app, parent)
    assert result['phase'] == 'completed' and result['engine'] == 'cloud'
    assert local.creates == 0 and cloud.creates == 1
    capabilities = await admin.get('/api/knowledge/capabilities')
    assert capabilities.status_code == 200 and capabilities.json()['mineru_available'] is True


async def test_partial_settings_update_preserves_endpoints_credentials_and_cloud_choice(
    admin: httpx.AsyncClient, app: FastAPI, miners: tuple[LocalMinerU, LocalMinerU]
) -> None:
    local, cloud = miners
    await configure(admin, local, cloud)
    current = (await admin.get('/api/knowledge/settings/parsing')).json()
    response = await admin.patch(
        '/api/knowledge/settings/parsing',
        json={
            'expected_version': current['version'],
            'timeout_seconds': 1200,
        },
    )
    assert response.status_code == 200
    saved = response.json()
    assert saved['local_url'] == local.url and saved['cloud_url'] == cloud.url
    assert saved['cloud_enabled'] and saved['has_local_key'] and saved['has_cloud_key']
    assert saved['tier'] == current['tier'] and saved['timeout_seconds'] == 1200
