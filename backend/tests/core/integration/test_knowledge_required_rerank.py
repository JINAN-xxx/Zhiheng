"""Reranking is mandatory across configuration, creation and successful retrieval."""

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_knowledge import configured_ranker, embedding_model
from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.integration.test_knowledge_repairs import settings_body
from tests.core.provider import ProviderHandler, ProviderServer
from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeDefaults, KnowledgeDocument, KnowledgeJob
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


async def finish_query(admin: httpx.AsyncClient, app: FastAPI, result: dict[str, Any]) -> dict[str, Any]:
    identifier = UUID(result['id'])
    for _ in range(40):
        await app.state.container.knowledge_preparation.tick(identifier)
        async with app.state.container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(app.state.container, run_id)
        result = (await admin.get('/api/knowledge/preparations/' + str(identifier))).json()
        if result['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
            return result
    raise AssertionError(str(result))


async def test_default_ranker_cannot_be_cleared_and_missing_defaults_block_creation(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await embedding_model(admin, provider)
    current = (await admin.get('/api/knowledge/settings')).json()
    ranker = current['default_rerank_model_id']
    rejected = await admin.patch(
        '/api/knowledge/settings', json={'expected_version': current['version'], 'default_rerank_model_id': None}
    )
    assert rejected.status_code == 422 and rejected.json()['error']['code'] == 'knowledge_rerank_required', (
        rejected.text
    )
    assert (await admin.get('/api/knowledge/settings')).json() == current
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(KnowledgeDefaults, 1)
        row.default_embed_model_id = UUID(model)
        row.default_rerank_model_id = None
    capabilities = (await admin.get('/api/knowledge/capabilities')).json()
    assert capabilities['default_embedding_configured'] and not capabilities['default_rerank_configured']
    for body in (
        {'name': 'Must not create'},
        {'name': 'Explicit embedding also needs rerank', 'embed_model_id': model},
    ):
        response = await admin.post('/api/knowledge/bases', json=body)
        assert response.status_code == 409 and response.json()['error']['code'] == 'knowledge_rerank_required', (
            response.text
        )
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeBase)) == 0
    saved = await admin.patch(
        '/api/knowledge/settings', json={'expected_version': current['version'], 'default_rerank_model_id': ranker}
    )
    assert saved.status_code == 200, saved.text
    capabilities = (await admin.get('/api/knowledge/capabilities')).json()
    assert capabilities['default_rerank_configured'] and capabilities['default_rerank_model_id'] == ranker
    assert (await admin.post('/api/knowledge/bases', json={'name': 'Ready'})).status_code == 201
    assert not provider.requests


async def test_disabling_rerank_is_rejected_and_switching_ranker_never_rebuilds_index(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    path = '/api/knowledge/bases/' + base['id']
    base = (await admin.get(path)).json()
    rejected = await admin.patch(
        path + '/settings', json=settings_body(base, use_default_rerank=False, rerank_model_id=None)
    )
    assert rejected.status_code == 422
    ranker = await configured_ranker(admin, provider)
    all_models = (await admin.get('/api/models')).json()
    original = next(row for row in all_models if row['id'] == ranker)
    replacement = await admin.post(
        '/api/models',
        json={
            'name': 'Replacement rerank',
            'connection_id': original['connection_id'],
            'upstream_model': 'replacement-rerank',
            'model_type': 'rerank',
        },
    )
    assert replacement.status_code == 201
    async with app.state.container.database.sessions() as session:
        jobs = await session.scalar(select(func.count()).select_from(KnowledgeJob))
        documents = list(
            await session.scalars(
                select(KnowledgeDocument.file_id).where(KnowledgeDocument.base_id == UUID(base['id']))
            )
        )
    calls = len(provider.requests)
    changed = await admin.patch(
        path + '/settings', json=settings_body(base, use_default_rerank=False, rerank_model_id=replacement.json()['id'])
    )
    assert changed.status_code == 200 and changed.json()['job'] is None, changed.text
    assert changed.json()['base']['active_revision'] == base['active_revision']
    assert changed.json()['base']['index_status'] == 'ready'
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeJob)) == jobs
        assert (
            list(
                await session.scalars(
                    select(KnowledgeDocument.file_id).where(KnowledgeDocument.base_id == UUID(base['id']))
                )
            )
            == documents
        )
    assert len(provider.requests) == calls


@pytest.mark.parametrize('state', ['missing', 'disabled', 'connection'])
async def test_query_rejects_unusable_rerank_before_paid_embedding(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, state: str
) -> None:
    base = await ready_base(admin, app, provider)
    ranker = await configured_ranker(admin, provider)
    if state == 'missing':
        async with app.state.container.database.sessions.begin() as session:
            (await session.get(KnowledgeDefaults, 1)).default_rerank_model_id = None
    elif state == 'connection':
        conn = await admin.post(
            '/api/models/connections',
            json={
                'name': 'Separate rerank connection',
                'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
                'api_key': 'local-test-key',
            },
        )
        assert conn.status_code == 201
        assert (
            await admin.patch('/api/models/' + ranker, json={'connection_id': conn.json()['id']})
        ).status_code == 200
        assert (
            await admin.post('/api/models/connections/' + conn.json()['id'] + '/disable', json={})
        ).status_code == 200
    else:
        assert (await admin.post('/api/models/' + ranker + '/disable', json={})).status_code == 200
    async with app.state.container.database.sessions() as session:
        runs = await session.scalar(select(func.count()).select_from(Run))
    calls = len(provider.requests)
    response = await admin.post(
        '/api/knowledge/bases/' + base['id'] + '/query',
        json={'query': 'service port', 'client_request_id': str(uuid4())},
    )
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] in {'knowledge_rerank_required', 'knowledge_rerank_unavailable'}
    assert len(provider.requests) == calls
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Run)) == runs


async def test_successful_query_completes_rerank_exactly_once(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    body = {'query': 'service port', 'client_request_id': str(uuid4())}
    response = await admin.post('/api/knowledge/bases/' + base['id'] + '/query', json=body)
    assert response.status_code == 202, response.text
    result = await finish_query(admin, app, response.json())
    assert result['status'] == 'completed' and result['hits'], result
    assert result['diagnostics']['rerank_status'] == 'completed'
    assert len([request for request in provider.requests if request['model'] == 'rerank']) == 1
    repeated = await admin.post('/api/knowledge/bases/' + base['id'] + '/query', json=body)
    assert repeated.json()['id'] == response.json()['id']
    assert len([request for request in provider.requests if request['model'] == 'rerank']) == 1


@pytest.mark.parametrize('entry', ['query', 'chat'])
async def test_transient_rerank_failure_never_returns_unranked_results_or_admits_chat(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
) -> None:
    base = await ready_base(admin, app, provider)
    original = ProviderHandler.structured

    def temporary(handler: ProviderHandler, body: dict[str, Any]) -> None:
        if handler.path.endswith('/rerank'):
            handler.send_response(503)
            handler.send_header('Content-Type', 'application/json')
            handler.end_headers()
            handler.wfile.write(json.dumps({'error': {'message': 'temporary'}}).encode())
        else:
            original(handler, body)

    monkeypatch.setattr(ProviderHandler, 'structured', temporary)
    if entry == 'query':
        response = await admin.post(
            '/api/knowledge/bases/' + base['id'] + '/query',
            json={'query': 'service port', 'client_request_id': str(uuid4())},
        )
    else:
        cid = (await admin.post('/api/chat/conversations', json={})).json()['id']
        response = await admin.post(
            '/api/chat/conversations/' + cid + '/runs',
            json={
                'model_id': model_id,
                'message': 'service port',
                'knowledge_base_ids': [base['id']],
                'client_request_id': str(uuid4()),
            },
        )
    assert response.status_code == 202, response.text
    result = await finish_query(admin, app, response.json())
    assert result['status'] == 'failed' and result['error']['code'] == 'knowledge_rerank_failed', result
    assert not result['hits']
    assert not [request for request in provider.requests if request.get('model') == 'local-text']
    assert len([request for request in provider.requests if request['model'] == 'rerank']) == 1
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'chat')) == 0
