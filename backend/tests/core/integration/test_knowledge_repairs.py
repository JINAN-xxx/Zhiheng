"""Real HTTP/database regressions for library settings and durable uploads."""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_knowledge_failures import finish_job, ready_base
from tests.core.integration.test_knowledge_materials_v2 import library, reader
from tests.core.provider import ProviderServer
from zhiheng.modules.files.orm import FileRecord, FileReference
from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeUpload, KnowledgeUploadReceipt
from zhiheng.modules.models.orm import ModelConnection
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow
from zhiheng.worker.execution import execute_run


def settings_body(base: dict, model: str | None = None, **extra: object) -> dict:
    return {
        'expected_version': base['version'],
        'embed_model_id': model or base['embed_model_id'],
        'rerank_model_id': None,
        'use_default_rerank': True,
        'chunk_size': base['chunk_size'],
        'chunk_overlap': base['chunk_overlap'],
        'confirm_rebuild': False,
        'client_request_id': str(uuid4()),
        **extra,
    }


async def test_connection_metadata_preserves_index_binding(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    model = next(m for m in (await admin.get('/api/models')).json() if m['id'] == base['embed_model_id'])
    cid = model['connection_id']
    async with app.state.container.database.sessions() as session:
        before = await app.state.container.models.resolve(session, UUID(model['id']))
    for fields in ({'name': 'Renamed only'}, {'enabled': False}, {'enabled': True}, {'api_key': 'local-test-key'}):
        result = await admin.patch(f'/api/models/connections/{cid}', json=fields)
        assert result.status_code == 200, result.text
    async with app.state.container.database.sessions() as session:
        after = await app.state.container.models.resolve(session, UUID(model['id']))
    assert before == after
    assert (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()['index_status'] == 'ready'


async def test_empty_old_index_accepts_new_binding_and_upload(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    path = f'/api/knowledge/bases/{base["id"]}'
    document = (await admin.get(path + '/documents')).json()['items'][0]
    deleted = await admin.request(
        'DELETE',
        path + '/documents/' + document['id'],
        json={'expected_version': document['version'], 'client_request_id': str(uuid4())},
    )
    assert (await finish_job(admin, app, deleted.json()))['status'] == 'completed'
    model = next(m for m in (await admin.get('/api/models')).json() if m['id'] == base['embed_model_id'])
    assert (
        await admin.patch('/api/models/connections/' + model['connection_id'], json={'api_key': 'changed-fixture-key'})
    ).status_code == 200
    uploaded = await admin.post(
        path + '/uploads',
        params={'filename': 'new.txt', 'client_request_id': str(uuid4())},
        content=b'new empty-library data',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    job = await admin.post(
        path + '/documents', json={'file_ids': [uploaded.json()['file']['id']], 'client_request_id': str(uuid4())}
    )
    assert job.status_code == 202, job.text
    assert (await finish_job(admin, app, job.json()))['status'] == 'completed'


async def test_settings_rebuild_confirms_replays_and_publishes_model_atomically(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    path = f'/api/knowledge/bases/{base["id"]}'
    base = (await admin.get(path)).json()
    original = base['active_revision']
    replacement = await embedding_model(admin, provider)
    body = settings_body(base, replacement, chunk_size=200, chunk_overlap=20)
    denied = await admin.patch(path + '/settings', json=body)
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'knowledge_rebuild_confirmation_required'
    body['confirm_rebuild'] = True
    accepted = await admin.patch(path + '/settings', json=body)
    assert accepted.status_code == 200, accepted.text
    value = accepted.json()
    assert value['job']['operation'] == 'rebuild'
    assert value['base']['embed_model_id'] == base['embed_model_id']
    assert value['base']['active_revision'] == original
    replay = await admin.patch(path + '/settings', json=body)
    assert replay.status_code == 200 and replay.json()['job']['id'] == value['job']['id']
    assert (await finish_job(admin, app, value['job']))['status'] == 'completed'
    current = (await admin.get(path)).json()
    assert current['embed_model_id'] == replacement and current['active_revision'] != original
    assert current['chunk_size'] == 200 and current['chunk_overlap'] == 20
    assert current['use_default_rerank'] and current['rerank_model_id'] is None
    async with app.state.container.database.sessions() as session:
        assert (
            await app.state.container.knowledge.retrieval_rerank_binding(session, [UUID(base['id'])])
        ).model_type == 'rerank'


async def test_failed_settings_rebuild_keeps_old_model_and_index(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = await ready_base(admin, app, provider)
    path = f'/api/knowledge/bases/{base["id"]}'
    base = (await admin.get(path)).json()
    replacement = await embedding_model(admin, provider)
    monkeypatch.setattr(
        app.state.container.knowledge_jobs.vectors,
        'build',
        AsyncMock(side_effect=AppError('fixture_index_failed', 'fixture', 503)),
    )
    response = await admin.patch(path + '/settings', json=settings_body(base, replacement, confirm_rebuild=True))
    assert response.status_code == 200, response.text
    failed = await finish_job(admin, app, response.json()['job'])
    assert failed['status'] == 'failed'
    current = (await admin.get(path)).json()
    assert current['embed_model_id'] == base['embed_model_id'] and current['active_revision'] == base['active_revision']


async def test_staging_retry_is_durable_concurrent_and_content_bound(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await library(admin, provider)
    path = f'/api/knowledge/bases/{base["id"]}'
    request_id = str(uuid4())

    async def upload(content: bytes = b'only one copy') -> httpx.Response:
        return await admin.post(
            path + '/uploads',
            params={'filename': 'once.txt', 'client_request_id': request_id},
            content=content,
            headers={'Content-Type': 'application/octet-stream'},
        )

    first, second = await asyncio.gather(upload(), upload())
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json() == second.json()
    conflict = await upload(b'different bytes')
    assert conflict.status_code == 409 and conflict.json()['error']['code'] == 'idempotency_conflict'
    status = await admin.get(path + '/uploads/requests/' + request_id)
    assert status.status_code == 200 and status.json()['file']['id'] == first.json()['file']['id']
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeUploadReceipt)) == 1
        assert await session.scalar(select(func.count()).select_from(KnowledgeUpload)) == 1
    removed = await admin.request('DELETE', path + '/uploads/' + first.json()['upload_id'], json={})
    assert removed.status_code == 204, removed.text
    assert (await upload()).status_code == 409


async def test_staging_cleanup_expires_receipt_and_retains_recycle_policy(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await library(admin, provider)
    key = str(uuid4())
    path = f'/api/knowledge/bases/{base["id"]}'
    response = await admin.post(
        path + '/uploads',
        params={'filename': 'abandoned.txt', 'client_request_id': key},
        content=b'abandoned original',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 201, response.text
    value = response.json()
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(KnowledgeUpload, UUID(value['upload_id']))
        row.created_at = utcnow() - timedelta(hours=25)
    await app.state.container.knowledge_documents.cleanup_uploads()
    async with app.state.container.database.sessions() as session:
        file = await session.get(FileRecord, UUID(value['file']['id']))
        assert file.status == 'deleted' and file.retention_until > utcnow()
        assert (
            await session.scalar(
                select(func.count()).select_from(FileReference).where(FileReference.file_id == file.id)
            )
            == 0
        )
    assert (await admin.get(path + '/uploads/requests/' + key)).status_code == 409


async def test_settings_reject_wrong_type_and_foreign_owner(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str
) -> None:
    base = await library(admin, provider)
    path = f'/api/knowledge/bases/{base["id"]}/settings'
    assert (await admin.patch(path, json=settings_body(base, model_id))).status_code == 422
    assert (
        await admin.patch(
            path, json=settings_body(base, use_default_rerank=False, rerank_model_id=base['embed_model_id'])
        )
    ).status_code == 422
    member = await reader(app)
    try:
        assert (await member.patch(path, json=settings_body(base))).status_code == 404
    finally:
        await member.aclose()


async def test_empty_legacy_migration_completes_without_model_calls(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await library(admin, provider)
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(KnowledgeBase, UUID(base['id']))
        row.document_policy = 'legacy'
    path = f'/api/knowledge/bases/{base["id"]}'
    preview = (await admin.get(path + '/migration')).json()
    accepted = await admin.post(
        path + '/migration',
        json={
            'expected_version': preview['expected_version'],
            'expected_acl_version': preview['acl_version'],
            'confirm_shared_access': True,
            'client_request_id': str(uuid4()),
        },
    )
    assert accepted.status_code == 202, accepted.text
    assert (await finish_job(admin, app, accepted.json()))['status'] == 'completed'
    assert (await admin.get(path)).json()['document_policy'] == 'library'
    assert not provider.requests


async def test_library_ranker_is_used_and_protected_by_current_references(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base = await ready_base(admin, app, provider)
    path = f'/api/knowledge/bases/{base["id"]}'
    base = (await admin.get(path)).json()
    model = next(m for m in (await admin.get('/api/models')).json() if m['id'] == base['embed_model_id'])
    created = await admin.post(
        '/api/models',
        json={
            'name': 'Library ranker',
            'connection_id': model['connection_id'],
            'upstream_model': 'library-ranker',
            'model_type': 'rerank',
            'max_output_tokens': 64,
        },
    )
    assert created.status_code == 201, created.text
    ranker = created.json()['id']
    configured = await admin.patch(
        path + '/settings', json=settings_body(base, use_default_rerank=False, rerank_model_id=ranker)
    )
    assert configured.status_code == 200 and configured.json()['job'] is None, configured.text
    admitted = await admin.post(path + '/query', json={'query': 'service port', 'client_request_id': str(uuid4())})
    assert admitted.status_code == 202, admitted.text
    identifier = admitted.json()['id']
    for _ in range(20):
        await app.state.container.knowledge_preparation.tick(UUID(identifier))
        async with app.state.container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(app.state.container, run_id)
        result = (await admin.get('/api/knowledge/preparations/' + identifier)).json()
        if result['status'] == 'completed':
            break
    assert result['status'] == 'completed', result
    assert result['diagnostics']['rerank_status'] == 'completed'
    assert len([request for request in provider.requests if request['model'] == 'library-ranker']) == 1
    removed = await admin.request('DELETE', '/api/models/' + ranker, json={})
    assert removed.status_code == 409, removed.text


async def test_replacing_unreadable_credentials_still_recovers_connection(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    model_id = await embedding_model(admin, provider)
    model = next(m for m in (await admin.get('/api/models')).json() if m['id'] == model_id)
    cid = model['connection_id']
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(ModelConnection, UUID(cid))
        row.encrypted_api_key = 'damaged-fixture-ciphertext'
    response = await admin.patch('/api/models/connections/' + cid, json={'api_key': 'replacement-fixture-key'})
    assert response.status_code == 200, response.text
    async with app.state.container.database.sessions() as session:
        row = await session.get(ModelConnection, UUID(cid))
        assert app.state.container.cipher.decrypt(row.encrypted_api_key) == 'replacement-fixture-key'
