"""Governed retrieval uses only the test HTTP provider and a deterministic index port."""

import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_knowledge import VectorFixture, embedding_model
from tests.core.integration.test_knowledge_failures import finish_job
from tests.core.provider import ProviderHandler, ProviderServer
from zhiheng.modules.knowledge.orm import IndexGeneration, KnowledgeDefaults, KnowledgeStep, RetrievalIntent
from zhiheng.modules.knowledge.public import VectorSpec
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


class HybridFixture(VectorFixture):
    def __init__(self) -> None:
        super().__init__()
        self.searches = []

    async def hybrid_search(
        self, spec: VectorSpec, vector: list[float], query: str, limit: int, document_ids: list[UUID], **kwargs: Any
    ) -> list[dict[str, Any]]:
        assert spec.retrieval_version == 2 and limit == 40
        self.searches.append((spec, document_ids))
        return await self.search(spec, vector, limit, document_ids, **kwargs)


async def ready(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> tuple[dict, str, str, HybridFixture]:
    container = app.state.container
    vectors = HybridFixture()
    container.knowledge_jobs.vectors = container.knowledge_preparation.vectors = vectors
    embed = await embedding_model(admin, provider)
    connection = (
        await admin.post(
            '/api/models/connections',
            json={'name': 'Ranker', 'base_url': f'http://127.0.0.1:{provider.server_port}/v1', 'api_key': 'mock-key'},
        )
    ).json()
    response = await admin.post(
        '/api/models',
        json={
            'name': 'Ranker',
            'connection_id': connection['id'],
            'upstream_model': 'ranker',
            'model_type': 'rerank',
            'max_output_tokens': 64,
        },
    )
    assert response.status_code == 201, response.text
    rerank = response.json()['id']
    async with container.database.sessions.begin() as session:
        defaults = await session.get(KnowledgeDefaults, 1)
        if defaults is None:
            defaults = KnowledgeDefaults(id=1)
            session.add(defaults)
        defaults.default_embed_model_id, defaults.default_rerank_model_id = UUID(embed), UUID(rerank)
    response = await admin.post('/api/knowledge/bases', json={'name': 'Retrieval v2'})
    assert response.status_code == 201, response.text
    base = response.json()
    for name, text in [('first.txt', '项目代号 星河 10086'), ('second.txt', '项目编号 8080 服务入口')]:
        uploaded = await admin.post(
            f'/api/knowledge/bases/{base["id"]}/uploads',
            params={'filename': name},
            content=text.encode(),
            headers={'content-type': 'application/octet-stream'},
        )
        assert uploaded.status_code == 201, uploaded.text
        response = await admin.post(
            f'/api/knowledge/bases/{base["id"]}/documents',
            json={'file_ids': [uploaded.json()['file']['id']], 'client_request_id': str(uuid4())},
        )
        assert response.status_code == 202, response.text
        job = await finish_job(admin, app, response.json())
        assert job['status'] == 'completed', job
    return base, embed, rerank, vectors


async def pending_ranker(admin: httpx.AsyncClient, app: FastAPI, base: dict[str, Any]) -> tuple[dict, UUID]:
    container = app.state.container
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/query', json={'query': '项目编号', 'client_request_id': str(uuid4())}
    )
    assert response.status_code == 202, response.text
    intent = response.json()
    for _ in range(8):
        await container.knowledge_preparation.tick(UUID(intent['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Run).where(Run.status == 'pending')))
        for run in pending:
            if run.resource_type == 'knowledge_rerank':
                return intent, run.id
            await execute_run(container, run.id)
    pytest.fail(str((await admin.get(f'/api/knowledge/preparations/{intent["id"]}')).json()))


async def test_ranker_is_durable_frozen_metered_and_replay_does_not_recall(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, embed, rerank, vectors = await ready(admin, app, provider)
    container = app.state.container
    original = ProviderHandler.structured

    def reversed_ranker(handler: ProviderHandler, body: dict[str, Any]) -> None:
        if not handler.path.endswith('/rerank'):
            original(handler, body)
            return
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(
            json.dumps(
                {
                    'results': [{'index': 1, 'relevance_score': 0.95}, {'index': 0, 'relevance_score': 0.1}],
                    'meta': {'billed_units': {'search_units': 2}},
                }
            ).encode()
        )

    monkeypatch.setattr(ProviderHandler, 'structured', reversed_ranker)
    intent, run_id = await pending_ranker(admin, app, base)
    async with container.database.sessions.begin() as session:
        defaults = await session.get(KnowledgeDefaults, 1)
        defaults.default_rerank_model_id = None
        row = await session.get(RetrievalIntent, UUID(intent['id']))
        assert row.dependencies['rerank_binding']['model_id'] == rerank
        original_order = [hit['chunk_id'] for hit in row.dependencies['retrieval_candidates']]
        assert len(original_order) == 2
        generation = await session.get(IndexGeneration, UUID(row.dependencies['bases'][0]['spec']['revision']))
        assert generation.retrieval_version == 2 and generation.binding['model_id'] == embed
    await execute_run(container, run_id)
    await container.knowledge_preparation.tick(UUID(intent['id']))
    result = await admin.get(f'/api/knowledge/preparations/{intent["id"]}')
    assert result.status_code == 200, result.text
    value = result.json()
    assert value['status'] == 'completed', value
    assert [hit['chunk_id'] for hit in value['hits']] == list(reversed(original_order))
    assert [hit['rerank_score'] for hit in value['hits']] == [0.95, 0.1]
    assert value['diagnostics']['rerank_status'] == 'completed'
    assert all(hit['fusion_score'] is not None and hit['vector_score'] is None for hit in value['hits'])
    assert len(vectors.searches) == 1, 'Resume must reuse the frozen candidates'
    for _ in range(2):
        await container.knowledge_preparation.tick(UUID(intent['id']))
        assert (await admin.get(f'/api/knowledge/preparations/{intent["id"]}')).json()['hits'] == value['hits']
    assert len([body for body in provider.requests if body['model'] == 'ranker']) == 1
    async with container.database.sessions() as session:
        steps = list(
            await session.scalars(
                select(KnowledgeStep).where(
                    KnowledgeStep.parent_id == UUID(intent['id']), KnowledgeStep.step_key == 'query_rerank'
                )
            )
        )
        assert len(steps) == 1 and steps[0].run_id == run_id
    usage = (await admin.get('/api/usage')).json()['records']
    assert any(record['model_id'] == rerank for record in usage), usage


@pytest.mark.parametrize('status', [429, 503, 403])
async def test_provider_failures_never_return_unranked_hits(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    base, _, _, _ = await ready(admin, app, provider)
    original = ProviderHandler.structured

    def failed_ranker(handler: ProviderHandler, body: dict[str, Any]) -> None:
        if not handler.path.endswith('/rerank'):
            original(handler, body)
            return
        handler.send_response(status)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(b'{"error":{"message":"mock provider failure"}}')

    monkeypatch.setattr(ProviderHandler, 'structured', failed_ranker)
    intent, run_id = await pending_ranker(admin, app, base)
    await execute_run(app.state.container, run_id)
    await app.state.container.knowledge_preparation.tick(UUID(intent['id']))
    value = (await admin.get(f'/api/knowledge/preparations/{intent["id"]}')).json()
    assert value['status'] == 'failed', value
    assert value['diagnostics']['rerank_status'] == 'failed'
    assert value['error']['code'] == 'knowledge_rerank_failed'
    assert value['hits'] == []


async def test_cancelled_preparation_never_sends_ranker_content(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base, _, _, _ = await ready(admin, app, provider)
    intent, run_id = await pending_ranker(admin, app, base)
    result = await admin.post(f'/api/knowledge/preparations/{intent["id"]}/cancel', json={})
    assert result.status_code == 200, result.text
    await execute_run(app.state.container, run_id)
    assert not [body for body in provider.requests if body['model'] == 'ranker']


async def test_removed_source_never_sends_ranker_content(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from zhiheng.modules.knowledge.orm import KnowledgeDocument
    from zhiheng.shared.time import utcnow

    base, _, _, _ = await ready(admin, app, provider)
    intent, run_id = await pending_ranker(admin, app, base)
    async with app.state.container.database.sessions.begin() as session:
        document = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.base_id == UUID(base['id'])))
        document.deleted_at = utcnow()
    await execute_run(app.state.container, run_id)
    await app.state.container.knowledge_preparation.tick(UUID(intent['id']))
    assert not [body for body in provider.requests if body['model'] == 'ranker']
    async with app.state.container.database.sessions() as session:
        row = await session.get(RetrievalIntent, UUID(intent['id']))
        assert row.status == 'failed' and not row.hits


async def test_api_key_must_explicitly_authorize_default_ranker(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from tests.core.integration.test_resource_key_scopes import application, issue
    from zhiheng.modules.knowledge.public import QueryRequest
    from zhiheng.shared.errors import AppError

    base, embed, _, _ = await ready(admin, app, provider)
    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['knowledge:query'], model_ids=[embed])
    c = app.state.container
    async with c.database.sessions.begin() as session:
        # Resolve the current developer identity using the same bearer authentication as API admission.
        developer = await c.keys.authenticate(session, key['api_key'])
        principal = await c.identity.principal_for_user(session, developer.owner_id)
        with pytest.raises(AppError) as caught:
            await c.knowledge_preparation.query(
                session,
                principal,
                UUID(base['id']),
                QueryRequest(query='项目', client_request_id=uuid4()),
                identity=developer,
            )
        assert caught.value.status_code in {403, 404}
    assert not [body for body in provider.requests if body['model'] == 'ranker']


async def test_changed_global_embedding_default_does_not_rebind_existing_index(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base, original_embed, _, _ = await ready(admin, app, provider)
    replacement = await embedding_model(admin, provider)
    c = app.state.container
    async with c.database.sessions.begin() as session:
        defaults = await session.get(KnowledgeDefaults, 1)
        defaults.default_embed_model_id = UUID(replacement)
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/query', json={'query': '查询绑定', 'client_request_id': str(uuid4())}
    )
    assert response.status_code == 202, response.text
    intent = response.json()
    await c.knowledge_preparation.tick(UUID(intent['id']))
    async with c.database.sessions() as session:
        row = await session.get(RetrievalIntent, UUID(intent['id']))
        assert row.dependencies['bases'][0]['binding']['model_id'] == original_embed
        step = await session.scalar(select(KnowledgeStep).where(KnowledgeStep.parent_id == row.id))
        run = await session.get(Run, step.run_id)
        assert run.payload['binding']['model_id'] == original_embed
        assert run.payload['binding']['model_id'] != replacement


async def test_no_candidates_returns_empty_without_claiming_rerank(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base, _, _, vectors = await ready(admin, app, provider)
    for revision in vectors.rows:
        vectors.rows[revision] = []
    c = app.state.container
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/query', json={'query': '没有匹配内容', 'client_request_id': str(uuid4())}
    )
    assert response.status_code == 202, response.text
    intent = response.json()
    for _ in range(3):
        await c.knowledge_preparation.tick(UUID(intent['id']))
        async with c.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run in pending:
            await execute_run(c, run)
    value = (await admin.get(f'/api/knowledge/preparations/{intent["id"]}')).json()
    assert value['status'] == 'completed' and value['hits'] == []
    assert value['diagnostics']['rerank_status'] == 'no_candidates'
    assert not [body for body in provider.requests if body['model'] == 'ranker']
