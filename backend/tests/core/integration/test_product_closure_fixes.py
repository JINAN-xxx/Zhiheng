"""Product workflows: idempotency, recovery, complete pricing and resource access."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_agents import config
from tests.core.integration.test_knowledge import VectorFixture, embedding_model
from tests.core.integration.test_knowledge_failures import finish_job
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.integration.test_work import member_client, published, task
from tests.core.integration.test_worker import named_model
from tests.core.provider import ProviderServer
from zhiheng.infrastructure.database import SCHEMA_REVISION
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


async def test_token_only_api_needs_no_currency_rate(admin: httpx.AsyncClient, model_id: str, app: FastAPI) -> None:
    price = await admin.post(
        f'/api/billing/models/{model_id}/rates',
        json={'billing_mode': 'token', 'is_free': True, 'client_request_id': str(uuid4())},
    )
    assert price.status_code == 201, price.text
    owner = await application(admin)
    key = await issue(admin, owner['id'], model_ids=[model_id])
    from tests.core.integration.test_public_protocol import drive

    pending = asyncio.create_task(
        admin.post(
            '/v1/chat/completions',
            headers={'Authorization': f'Bearer {key["api_key"]}'},
            json={'model': model_id, 'messages': [{'role': 'user', 'content': 'hello'}]},
        )
    )
    response = await drive(app.state.container, pending)
    assert response.status_code == 200, response.text


async def test_unknown_token_usage_can_be_reconciled_exactly_once(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await named_model(admin, provider, 'no-usage')
    accepted = await admin.post(
        f'/api/models/{model}/trial', json={'prompt': 'hello', 'client_request_id': str(uuid4())}
    )
    assert accepted.status_code == 202, accepted.text
    run = accepted.json()['id']
    await execute_run(app.state.container, UUID(run))
    before = (await admin.get('/api/usage')).json()
    row = before['records'][0]
    assert row['status'] == 'unknown' and before['reserved_tokens'] == 0
    assert row['points_enforced'] is False and before['points_enabled'] is False
    body = {
        'expected_version': row['version'],
        'reason': 'provider verified',
        'client_request_id': str(uuid4()),
        'input_tokens': 8,
        'output_tokens': 2,
    }
    async with member_client(app) as member:
        assert (await member.post('/api/usage/' + run + '/reconcile', json=body)).status_code == 403
    result = await admin.post('/api/usage/' + run + '/reconcile', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'known'
    assert (await admin.post('/api/usage/' + run + '/reconcile', json=body)).json() == result.json()
    after = (await admin.get('/api/usage')).json()
    assert after['reserved_tokens'] == 0 and after['used_tokens'] == 10
    conflict = await admin.post('/api/usage/' + run + '/reconcile', json={**body, 'input_tokens': 99})
    assert conflict.status_code == 409


async def test_quota_updates_are_authorized_versioned_and_idempotent(admin: httpx.AsyncClient, app: FastAPI) -> None:
    owner = (await admin.get('/api/auth/me')).json()['id']
    body = {'expected_version': 0, 'limit_tokens': 200000, 'reason': 'allocation', 'client_request_id': str(uuid4())}
    async with member_client(app) as member:
        assert (await member.patch('/api/usage/accounts/' + owner + '/quota', json=body)).status_code == 403
        assert (await member.get('/api/usage', params={'user_id': owner})).status_code == 403
    result = await admin.patch('/api/usage/accounts/' + owner + '/quota', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['quota_version'] == 1 and result.json()['limit_tokens'] == 200000
    assert (await admin.patch('/api/usage/accounts/' + owner + '/quota', json=body)).json() == result.json()
    stale = await admin.patch(
        '/api/usage/accounts/' + owner + '/quota',
        json={**body, 'client_request_id': str(uuid4()), 'limit_tokens': 300000},
    )
    assert stale.status_code == 409
    bodies = [
        {**body, 'expected_version': 1, 'limit_tokens': 300000 + i, 'client_request_id': str(uuid4())} for i in range(2)
    ]
    responses = await asyncio.gather(*(admin.patch('/api/usage/accounts/' + owner + '/quota', json=b) for b in bodies))
    assert sorted(r.status_code for r in responses) == [200, 409]


async def test_usage_server_pagination_and_filters_keep_old_records(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    from zhiheng.modules.billing.orm import Invocation

    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    async with app.state.container.database.sessions.begin() as session:
        for i in range(125):
            session.add(
                Invocation(
                    user_id=owner,
                    model_id=UUID(model_id),
                    run_id=uuid4(),
                    source='knowledge_retrieval' if i % 2 else 'chat',
                    status='known',
                    reserved_tokens=0,
                    input_tokens=1,
                    output_tokens=1,
                    charged_tokens=2,
                )
            )
    first = (await admin.get('/api/usage', params={'page_size': 20})).json()
    last = (await admin.get('/api/usage', params={'page_size': 20, 'page': 7})).json()
    filtered = (await admin.get('/api/usage', params={'page_size': 100, 'source': 'knowledge_retrieval'})).json()
    assert first['total'] == 125 and len(first['records']) == 20
    assert len(last['records']) == 5
    assert filtered['total'] == 62 and all(row['source'] == 'knowledge_retrieval' for row in filtered['records'])
    assert not ({row['id'] for row in first['records']} & {row['id'] for row in last['records']})


@pytest.mark.parametrize('status', ['completed', 'archived'])
async def test_work_can_repair_members_before_reopening(admin: httpx.AsyncClient, model_id: str, status: str) -> None:
    old = await published(admin, model_id)
    t = await task(admin, old)
    path = '/api/work/tasks/' + t['id']
    closed = await admin.patch(path, json={'status': status, 'expected_version': t['version']})
    assert closed.status_code == 200, closed.text
    deleted = await admin.delete('/api/agents/' + old['id'], headers={'Content-Type': 'application/json'})
    assert deleted.status_code == 200, deleted.text
    blocked = await admin.patch(path, json={'status': 'active', 'expected_version': closed.json()['version']})
    assert blocked.status_code == 409
    new = await published(admin, model_id, name='Replacement')
    added = await admin.post(
        path + '/members', json={'agent_id': new['id'], 'expected_version': closed.json()['version']}
    )
    assert added.status_code == 200, added.text
    removed = await admin.delete(
        path + '/members/' + old['id'],
        params={'expected_version': added.json()['version']},
        headers={'Content-Type': 'application/json'},
    )
    assert removed.status_code == 200, removed.text
    opened = await admin.patch(path, json={'status': 'active', 'expected_version': removed.json()['version']})
    assert opened.status_code == 200, opened.text
    assert len(opened.json()['messages']) == len(t['messages'])


async def test_agent_create_request_prevents_duplicates_and_rejects_changed_retries(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    body = {**config(model_id), 'client_request_id': str(uuid4())}
    first, second = await asyncio.gather(admin.post('/api/agents', json=body), admin.post('/api/agents', json=body))
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json()['id'] == second.json()['id']
    conflict = await admin.post('/api/agents', json={**body, 'name': 'different'})
    assert conflict.status_code == 409


async def test_agent_access_inventory_includes_effective_ranker(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str
) -> None:
    from tests.core.integration.test_knowledge_failures import ready_base

    base = await ready_base(admin, app, provider)
    a = await published(admin, model_id, knowledge_base_ids=[base['id']])
    metadata = await admin.get('/api/agents/' + a['id'] + '/api-access')
    assert metadata.status_code == 200, metadata.text
    ranker = (await admin.get('/api/knowledge/settings')).json()['default_rerank_model_id']
    assert set(metadata.json()['model_ids']) == {model_id, base['embed_model_id'], ranker}
    key = await issue(admin, a['application_id'], model_ids=metadata.json()['model_ids'])
    call = await admin.post(
        '/v1/agents/' + a['id'] + '/chat/completions',
        headers={'Authorization': f'Bearer {key["api_key"]}'},
        json={'messages': [{'role': 'user', 'content': 'port'}], 'client_request_id': str(uuid4())},
    )
    assert call.status_code == 202, call.text


async def test_pending_uploads_can_be_discovered_without_browser_state(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    embed = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Recovery', 'embed_model_id': embed})).json()
    path = '/api/knowledge/bases/' + base['id'] + '/uploads'
    upload = await admin.post(
        path,
        params={'filename': 'pending.txt', 'client_request_id': str(uuid4())},
        content=b'pending source',
        headers={'content-type': 'application/octet-stream'},
    )
    assert upload.status_code == 201, upload.text
    async with member_client(app) as member:
        assert (await member.get(path)).status_code == 404
    discovered = await admin.get(path)
    assert discovered.status_code == 200, discovered.text
    assert discovered.json()['total'] == 1
    item = discovered.json()['items'][0]
    assert item['file']['id'] == upload.json()['file']['id']
    discarded = await admin.request('DELETE', path + '/' + item['upload_id'], json={})
    assert discarded.status_code == 204, discarded.text
    assert (await admin.get(path)).json()['total'] == 0
    app.state.container.knowledge_jobs.vectors = VectorFixture()
    uploaded = await admin.post(
        path,
        params={'filename': 'imported.txt'},
        content=b'imported source',
        headers={'content-type': 'application/octet-stream'},
    )
    admitted = await admin.post(
        '/api/knowledge/bases/' + base['id'] + '/documents',
        json={'file_ids': [uploaded.json()['file']['id']], 'client_request_id': str(uuid4())},
    )
    assert admitted.status_code == 202, admitted.text
    job = await finish_job(admin, app, admitted.json())
    assert job['status'] == 'completed', job
    assert (await admin.get(path)).json()['total'] == 0


async def chinese_corpus(admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer) -> dict[str, Any]:
    c = app.state.container
    c.knowledge_jobs.vectors = c.knowledge_preparation.vectors = VectorFixture()
    embed = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Chinese', 'embed_model_id': embed})).json()
    path = '/api/knowledge/bases/' + base['id']
    text = ''.join(f'资料段落{i:04d}' + ('知' * 992) for i in range(42))
    upload = await admin.post(
        path + '/uploads',
        params={'filename': 'chinese.txt'},
        content=text.encode(),
        headers={'content-type': 'application/octet-stream'},
    )
    assert upload.status_code == 201, upload.text
    accepted = await admin.post(
        path + '/documents', json={'file_ids': [upload.json()['file']['id']], 'client_request_id': str(uuid4())}
    )
    job = await finish_job(admin, app, accepted.json())
    assert job['status'] == 'completed', job
    return base


@pytest.mark.parametrize('failure', [None, 'provider', 'tamper'])
async def test_long_chinese_candidates_all_finish_rerank_with_default_quota(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    base = await chinese_corpus(admin, app, provider)
    c = app.state.container
    path = '/api/knowledge/bases/' + base['id']
    from tests.core.provider import ProviderHandler

    original_structured = ProviderHandler.structured
    rank_calls = 0

    def fail_second_ranker(handler: ProviderHandler, body: dict) -> None:
        nonlocal rank_calls
        if handler.path.endswith('/rerank'):
            rank_calls += 1
            if failure == 'provider' and rank_calls == 2:
                handler.send_response(503)
                handler.send_header('Content-Type', 'application/json')
                handler.end_headers()
                handler.wfile.write(b'{"error":{"message":"local test failure"}}')
                return
        original_structured(handler, body)

    monkeypatch.setattr(ProviderHandler, 'structured', fail_second_ranker)
    rank_steps = 0
    before = len(provider.requests)
    accepted = await admin.post(path + '/query', json={'query': '知识内容', 'client_request_id': str(uuid4())})
    assert accepted.status_code == 202, accepted.text
    done = accepted.json()
    for _ in range(15):
        await c.knowledge_preparation.tick(UUID(done['id']))
        async with c.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run in pending:
            async with c.database.sessions.begin() as session:
                row = await session.get(Run, run)
                if row.resource_type == 'knowledge_rerank':
                    rank_steps += 1
                    if failure == 'tamper' and rank_steps == 2:
                        payload = dict(row.payload)
                        payload['messages'] = [dict(message) for message in row.payload['messages']]
                        payload['messages'][1]['content'] = 'tampered content outside frozen batch'
                        row.payload = payload
            await execute_run(c, run)
        done = (await admin.get('/api/knowledge/preparations/' + done['id'])).json()
        if done['status'] in {'completed', 'admitted', 'failed'}:
            break
    reranks = [body for body in provider.requests[before:] if 'documents' in body]
    if failure:
        assert done['status'] == 'failed' and done['hits'] == [], done
        assert done['diagnostics']['rerank_status'] == 'failed'
        assert len(reranks) == (2 if failure == 'provider' else 1)
    else:
        assert done['status'] == 'completed', done
        assert len(reranks) > 1 and sum(len(body['documents']) for body in reranks) == 40
        assert done['diagnostics']['rerank_status'] == 'completed'
    calls = len(provider.requests)
    await c.knowledge_preparation.tick(UUID(done['id']))
    assert len(provider.requests) == calls


async def test_schema25_upgrade_preserves_existing_usage_and_agent_data(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from tests.core.legacy_schema import reconstruct
    from zhiheng.bootstrap.migrations import migrate, migration_directory

    c = app.state.container
    accepted = await admin.post(
        '/api/models/' + model_id + '/trial', json={'prompt': 'migration fixture', 'client_request_id': str(uuid4())}
    )
    assert accepted.status_code == 202, accepted.text
    await execute_run(c, UUID(accepted.json()['id']))
    created = await admin.post('/api/agents', json=config(model_id))
    assert created.status_code == 201, created.text
    columns = {
        'developer_accounts': 'user_id,limit_tokens,used_tokens,reserved_tokens',
        'developer_invocations': 'id,run_id,user_id,model_id,source,status,reserved_tokens,input_tokens,output_tokens,charged_tokens',
        'agents': 'id,owner_id,config,version,publication_count,published_version,created_at,updated_at,deleted_at',
    }
    async with c.database.sessions.begin() as session:
        before = {
            table: (await session.execute(text('SELECT ' + fields + ' FROM ' + table + ' ORDER BY 1'))).all()
            for table, fields in columns.items()
        }
        migration_config = Config()
        migration_config.set_main_option('script_location', str(migration_directory()))

        def historical_schema(connection: Any) -> None:
            migration_config.attributes['connection'] = connection
            command.upgrade(migration_config, '0024_knowledge_rerank_required')

        await reconstruct(await session.connection(), historical_schema, preserve_personal_skills=True)
        assert await session.scalar(text('SELECT version_num FROM alembic_version')) == '0024_knowledge_rerank_required'
        assert await session.scalar(text("SELECT to_regclass('public.document_parse_settings') IS NULL"))
        assert await session.scalar(text("SELECT to_regclass('public.billing_token_commands') IS NULL"))
    await c.database.engine.dispose()
    await migrate(c.settings)
    async with c.database.sessions() as session:
        after = {
            table: (await session.execute(text('SELECT ' + fields + ' FROM ' + table + ' ORDER BY 1'))).all()
            for table, fields in columns.items()
        }
        assert after == before
        assert await session.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
