"""Agent retrieval limits and policy visibility through real console and worker paths."""

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_agents import config
from tests.core.integration.test_knowledge import VectorFixture, embedding_model, finish_preparation
from tests.core.integration.test_knowledge_failures import finish_job, ready_base, source
from tests.core.integration.test_work import published, task, turn
from tests.core.knowledge_support import knowledge_models
from tests.core.provider import ProviderHandler, ProviderServer
from zhiheng.modules.agents.orm import Agent, AgentVersion
from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeDefaults, RetrievalIntent


async def test_policy_reports_real_selection_and_authorizes_every_library(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    embed = await embedding_model(admin, provider)
    bases = [
        (await admin.post('/api/knowledge/bases', json={'name': name, 'embed_model_id': embed})).json()
        for name in ('A', 'B')
    ]
    default = (await admin.get('/api/knowledge/settings')).json()['default_rerank_model_id']
    endpoint = '/api/agents/knowledge-policy'
    body = {'knowledge_base_ids': [base['id'] for base in bases]}
    assert (await admin.post(endpoint, json=body)).json() == {'model_id': default, 'source': 'platform_default'}
    async with app.state.container.database.sessions.begin() as session:
        first = await session.get(KnowledgeBase, UUID(bases[0]['id']))
        first.use_default_rerank, first.rerank_model_id = False, UUID(default)
    assert (await admin.post(endpoint, json=body)).json() == {
        'model_id': default,
        'source': 'mixed_platform_default',
    }
    single = {'knowledge_base_ids': [bases[0]['id']]}
    assert (await admin.post(endpoint, json=single)).json() == {'model_id': default, 'source': 'knowledge_base'}
    async with app.state.container.database.sessions.begin() as session:
        second = await session.get(KnowledgeBase, UUID(bases[1]['id']))
        second.use_default_rerank, second.rerank_model_id = False, UUID(default)
    assert (await admin.post(endpoint, json=body)).json()['source'] == 'knowledge_base'
    for ids in ([], [bases[0]['id']] * 2, [str(uuid4())] * 21):
        assert (await admin.post(endpoint, json={'knowledge_base_ids': ids})).status_code == 422
    assert (await admin.post(endpoint, json={'knowledge_base_ids': [str(uuid4())]})).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        denied = await member.post(endpoint, json=body)
        assert denied.status_code == 403 and 'model_id' not in denied.json(), denied.text
        async with app.state.container.database.sessions.begin() as session:
            await app.state.container.accounts.create_user(
                session, username='other-admin', password='platform-test-password', is_admin=True
            )
        await member.post('/api/auth/login', json={'username': 'other-admin', 'password': 'platform-test-password'})
        denied = await member.post(endpoint, json=body)
        assert denied.status_code == 404 and 'model_id' not in denied.json(), denied.text
    assert not provider.requests, 'Policy inspection must never call a paid provider'


@pytest.mark.parametrize('failure', ['missing', 'disabled', 'connection'])
async def test_save_and_publish_validate_rerank_without_paid_calls(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str, failure: str
) -> None:
    base = await ready_base(admin, app, provider)
    body = config(model_id, knowledge_base_ids=[base['id']])
    agent = (await admin.post('/api/agents', json=body)).json()
    ranker = (await admin.get('/api/knowledge/settings')).json()['default_rerank_model_id']
    if failure == 'missing':
        async with app.state.container.database.sessions.begin() as session:
            (await session.get(KnowledgeDefaults, 1)).default_rerank_model_id = None
    elif failure == 'disabled':
        assert (await admin.post(f'/api/models/{ranker}/disable', json={})).status_code == 200
    else:
        connection = (
            await admin.post(
                '/api/models/connections',
                json={
                    'name': 'Separate ranker',
                    'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
                    'api_key': 'test-only',
                },
            )
        ).json()['id']
        assert (await admin.patch(f'/api/models/{ranker}', json={'connection_id': connection})).status_code == 200
        assert (await admin.post(f'/api/models/connections/{connection}/disable', json={})).status_code == 200
    calls = len(provider.requests)
    for response in (
        await admin.post('/api/agents/knowledge-policy', json={'knowledge_base_ids': [base['id']]}),
        await admin.post('/api/agents', json=body),
        await admin.patch(f'/api/agents/{agent["id"]}', json={**body, 'expected_version': 1}),
        await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1}),
    ):
        assert response.status_code == 409, response.text
        assert response.json()['error']['code'] in {'knowledge_rerank_required', 'knowledge_rerank_unavailable'}
    assert len(provider.requests) == calls
    unchanged = (await admin.get(f'/api/agents/{agent["id"]}')).json()
    assert unchanged['version'] == 1 and unchanged['published_version'] is None


@pytest.mark.parametrize(
    'entry,limit', [('preview', 1), ('preview', 8), ('preview', 15), ('work', 15), ('conversation', 15), ('public', 15)]
)
async def test_final_limit_reaches_ranker_and_bounds_the_combined_library_result(  # noqa: PLR0915 - verify each entry's publication and execution in the same isolated fixture
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
    limit: int,
) -> None:
    container = app.state.container
    vectors = VectorFixture()
    container.knowledge_jobs.vectors = container.knowledge_preparation.vectors = vectors
    embed = await embedding_model(admin, provider)
    base_ids = []
    for name in ('A', 'B'):
        base = (await admin.post('/api/knowledge/bases', json={'name': name, 'embed_model_id': embed})).json()
        content = ('\n'.join(f'{name} paragraph {index}: ' + 'service port 8080. ' * 5 for index in range(16))).encode()
        file = await source(admin, name + '.txt', content)
        response = await admin.post(
            f'/api/knowledge/bases/{base["id"]}/documents',
            json={'file_ids': [file['id']], 'chunk_size': 100, 'chunk_overlap': 0, 'client_request_id': str(uuid4())},
        )
        assert response.status_code == 202, response.text
        assert (await finish_job(admin, app, response.json()))['status'] == 'completed'
        base_ids.append(base['id'])
    original = ProviderHandler.structured

    def ranked(handler: ProviderHandler, body: dict[str, Any]) -> None:
        if not handler.path.endswith('/rerank'):
            original(handler, body)
            return
        count = min(len(body['documents']), body['top_n'])
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/json')
        handler.end_headers()
        handler.wfile.write(
            json.dumps(
                {
                    'results': [{'index': index, 'relevance_score': 1 - index / 100} for index in range(count)],
                    'meta': {'billed_units': {'search_units': len(body['documents'])}},
                }
            ).encode()
        )

    monkeypatch.setattr(ProviderHandler, 'structured', ranked)
    agent = await published(admin, model_id, knowledge_base_ids=base_ids, knowledge_top_k=limit)
    headers = None
    if entry == 'public':
        models = await knowledge_models(admin, model_id, embed)
        model_types = {row['id']: row['model_type'] for row in (await admin.get('/api/models')).json()}
        for identifier in models:
            response = await admin.post(
                f'/api/models/{identifier}/rate-cards',
                json=(
                    {'billing_mode': 'search_unit', 'search_price': '0.000001'}
                    if model_types[identifier] == 'rerank'
                    else {'billing_mode': 'token', 'input_price': '0.000001', 'output_price': '0.000001'}
                ),
            )
            assert response.status_code == 201, response.text
        key = (
            await admin.post(
                f'/api/developer/applications/{agent["application_id"]}/keys',
                json={'name': 'Knowledge limit', 'model_ids': models},
            )
        ).json()
        headers = {'Authorization': 'Bearer ' + key['api_key']}
    # Editing a draft must not change the published or frozen work member's limit.
    if entry != 'preview':
        edited = await admin.patch(
            f'/api/agents/{agent["id"]}',
            json=config(model_id, knowledge_base_ids=base_ids, knowledge_top_k=1, expected_version=2),
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()['published_config']['knowledge_top_k'] == limit
    body = {'messages': [{'role': 'user', 'content': 'service port'}], 'client_request_id': str(uuid4())}
    if entry == 'work':
        work = await task(admin, agent, goal='service port')
        response = await admin.post(f'/api/work/tasks/{work["id"]}/runs', json=turn(work, agent))
    elif entry == 'conversation':
        conversation = (
            await admin.post(f'/api/agents/{agent["id"]}/conversations', json={'client_request_id': str(uuid4())})
        ).json()
        response = await admin.post(
            f'/api/agents/{agent["id"]}/conversations/{conversation["id"]}/runs',
            json={'message': 'service port', 'client_request_id': str(uuid4())},
        )
    elif entry == 'public':
        response = await admin.post(f'/v1/agents/{agent["id"]}/chat/completions', json=body, headers=headers)
    else:
        response = await admin.post(f'/api/agents/{agent["id"]}/preview', json=body)
    assert response.status_code == 202, response.text
    async with container.database.sessions() as session:
        intent = await session.get(RetrievalIntent, UUID(response.json()['id']))
        assert intent.request['top_k'] == limit
    result = await finish_preparation(admin, app, response.json(), headers)
    assert len(result['hits']) == limit, result
    calls = [row for row in provider.requests if row['model'] == 'rerank']
    assert calls and all(call['top_n'] == limit for call in calls)
    assert all(len(call['documents']) > limit for call in calls), 'The candidate pool must stay larger than final hits'
    if limit == 15:
        assert {hit['knowledge_base_id'] for hit in result['hits']} == set(base_ids)


async def test_legacy_draft_and_publication_default_to_eight(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    original = config(model_id, client_request_id=str(uuid4()))
    agent = (await admin.post('/api/agents', json=original)).json()
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(Agent, UUID(agent['id']))
        legacy = {key: value for key, value in row.config.items() if key != 'knowledge_top_k'}
        row.config = legacy
        row.create_fingerprint = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
        row.published_version = row.publication_count = 1
        session.add(AgentVersion(agent_id=row.id, version=1, draft_version=1, config=legacy, skills=[]))
    response = await admin.get(f'/api/agents/{agent["id"]}')
    assert response.status_code == 200, response.text
    assert response.json()['knowledge_top_k'] == response.json()['published_config']['knowledge_top_k'] == 8
    replay = await admin.post('/api/agents', json=original)
    assert replay.status_code == 201 and replay.json()['id'] == agent['id'], replay.text
    changed = await admin.post('/api/agents', json={**original, 'knowledge_top_k': 15})
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'idempotency_conflict'
