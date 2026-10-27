import hashlib
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from tests.core.provider import ProviderServer
from tests.core.resources import resource
from zhiheng.modules.knowledge.public import MilvusStore, VectorSpec
from zhiheng.shared.errors import AppError


@pytest_asyncio.fixture
async def real_vectors(app: FastAPI) -> AsyncIterator[MilvusStore]:
    from sqlalchemy import select

    from zhiheng.modules.knowledge.orm import IndexGeneration

    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus URI required')
    container = app.state.container
    class RegisteredStore(MilvusStore):
        async def build(self,spec: VectorSpec,rows: list[dict[str,object]],**kwargs: Any) -> None:
            resource('intent','milvus_collection',spec.collection_name,description=spec.description(),database='default')
            await super().build(spec,rows,**kwargs)
            resource('created','milvus_collection',spec.collection_name)
    store = RegisteredStore(uri)
    container.knowledge_jobs.vectors = container.knowledge_preparation.vectors = store
    try:
        yield store
    finally:
        async with container.database.sessions() as session:
            generations = list(await session.scalars(select(IndexGeneration)))
        for generation in generations:
            if generation.dimension:
                await store.drop(
                    VectorSpec(
                        generation.base_id,
                        generation.id,
                        generation.attempt_id,
                        generation.collection_name,
                        generation.fingerprint,
                        generation.dimension,
                    )
                )
                resource('deleted','milvus_collection',generation.collection_name)


@pytest_asyncio.fixture
async def completed_public_knowledge(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
    real_vectors: MilvusStore,
) -> dict[str, Any]:
    from tests.core.integration.test_knowledge import embedding_model, finish_preparation
    from tests.core.integration.test_knowledge_failures import finish_job, source
    from tests.core.integration.test_work import published

    embedding = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Replay', 'embed_model_id': embedding})).json()
    file = await source(admin, 'replay.txt', b'An admitted answer remains recoverable.')
    job = await finish_job(
        admin,
        app,
        (
            await admin.post(
                f'/api/knowledge/bases/{base["id"]}/documents',
                json={
                    'file_ids': [file['id']],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json(),
    )
    assert job['status'] == 'completed', str(job)
    for model in (model_id, embedding):
        assert (
            await admin.post(
                f'/api/models/{model}/rate-cards',
                json={
                    'billing_mode': 'token',
                    'input_price': '0.000001',
                    'output_price': '0.000001',
                },
            )
        ).status_code == 201
    agent = await published(admin, model_id, knowledge_base_ids=[base['id']])
    key = (
        await admin.post(
            f'/api/developer/applications/{agent["application_id"]}/keys',
            json={
                'name': 'Recovery',
                'model_ids': [model_id, embedding],
            },
        )
    ).json()
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    body = {'messages': [{'role': 'user', 'content': 'What remains recoverable?'}], 'client_request_id': str(uuid4())}
    endpoint = f'/v1/agents/{agent["id"]}/chat/completions'
    first = await admin.post(endpoint, headers=headers, json=body)
    assert first.status_code == 202, first.text
    intent = await finish_preparation(admin, app, first.json(), headers)
    assert intent['run']['status'] == 'completed', str(intent)
    return {
        'agent': agent,
        'key': key,
        'headers': headers,
        'body': body,
        'endpoint': endpoint,
        'intent': intent,
        'model_id': model_id,
        'embedding': embedding,
    }


@pytest.mark.parametrize('change', ['model_disabled', 'connection_disabled', 'binding_changed'])
async def test_public_accepted_replay_uses_original_authorization_not_model_liveness(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    completed_public_knowledge: dict[str, Any],
    change: str,
) -> None:
    from sqlalchemy import func, select

    from zhiheng.modules.knowledge.orm import RetrievalIntent

    context = completed_public_knowledge
    before = (await admin.post(context['endpoint'], headers=context['headers'], json=context['body'])).json()
    model_id = context['model_id']
    if change == 'connection_disabled':
        connection = next(
            row['connection_id'] for row in (await admin.get('/api/models')).json() if row['id'] == model_id
        )
        response = await admin.patch('/api/models/connections/' + connection, json={'enabled': False})
    else:
        response = await admin.patch(
            '/api/models/' + model_id,
            json=({'enabled': False} if change == 'model_disabled' else {'upstream_model': 'new-binding'}),
        )
    assert response.status_code == 200, response.text
    count = len(provider.requests)
    replay = await admin.post(context['endpoint'], headers=context['headers'], json=context['body'])
    polled = await admin.get(context['intent']['polling_url'], headers=context['headers'])
    assert replay.status_code == 200 and replay.json()['id'] == before['id'], replay.text
    assert polled.status_code == 200 and polled.json()['run']['id'] == context['intent']['run']['id'], polled.text
    for changes in ({'messages': [{'role': 'user', 'content': 'Different'}]}, {'stream': True}):
        conflict = await admin.post(
            context['endpoint'], headers=context['headers'], json={**context['body'], **changes}
        )
        assert conflict.status_code == 409, conflict.text
    for _ in range(2):
        cancelled = await admin.post(context['intent']['polling_url'] + '/cancel', headers=context['headers'], json={})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()['run']['id'] == context['intent']['run']['id']
        assert cancelled.json()['run']['status'] == 'completed'
        assert cancelled.json()['hits'] == context['intent']['hits']
    if change != 'binding_changed':
        container = app.state.container
        async with container.database.sessions() as session:
            intents_before = await session.scalar(select(func.count()).select_from(RetrievalIntent))
        fresh = await admin.post(
            context['endpoint'],
            headers=context['headers'],
            json={
                **context['body'],
                'client_request_id': str(uuid4()),
            },
        )
        assert fresh.status_code == 404, fresh.text
        async with container.database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(RetrievalIntent)) == intents_before
    assert len(provider.requests) == count


@pytest.mark.parametrize(
    ('revoke', 'status'),
    [('key', 401), ('application', 401), ('grant', 404), ('agent', 404), ('rebind', 404), ('other_key', 404)],
)
async def test_public_accepted_replay_still_requires_current_authorization(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    completed_public_knowledge: dict[str, Any],
    revoke: str,
    status: int,
) -> None:
    context = completed_public_knowledge
    assert context['intent']['run']['text'] and context['intent']['hits'][0]['content']
    application = context['agent']['application_id']
    key_url = f'/api/developer/applications/{application}/keys/{context["key"]["id"]}'
    if revoke == 'key':
        response = await admin.post(key_url + '/revoke', json={})
    elif revoke == 'application':
        response = await admin.post(f'/api/developer/applications/{application}/disable', json={})
    elif revoke == 'grant':
        from uuid import UUID

        from zhiheng.modules.developer.orm import DeveloperKey

        async with app.state.container.database.sessions.begin() as session:
            key = await session.get(DeveloperKey, UUID(context['key']['id']), with_for_update=True)
            key.model_ids = [context['embedding']]
        response = None
    elif revoke == 'agent':
        agent = (await admin.get('/api/agents/' + context['agent']['id'])).json()
        response = await admin.post(f'/api/agents/{agent["id"]}/unpublish', json={'expected_version': agent['version']})
    elif revoke == 'rebind':
        from tests.core.integration.test_agents import config

        application = (
            await admin.post('/api/developer/applications', json={'code': 'rebind-' + uuid4().hex, 'name': 'Rebound'})
        ).json()['id']
        agent = (await admin.get('/api/agents/' + context['agent']['id'])).json()
        edited = await admin.patch(
            f'/api/agents/{agent["id"]}',
            json=config(
                context['model_id'],
                application_id=application,
                knowledge_base_ids=agent['knowledge_base_ids'],
                expected_version=agent['version'],
            ),
        )
        assert edited.status_code == 200, edited.text
        response = await admin.post(
            f'/api/agents/{agent["id"]}/publish', json={'expected_version': edited.json()['version']}
        )
    else:
        key = (
            await admin.post(
                f'/api/developer/applications/{application}/keys',
                json={'name': 'Other', 'model_ids': [context['model_id'], context['embedding']]},
            )
        ).json()
        context['headers'] = {'Authorization': 'Bearer ' + key['api_key']}
        response = None
    if response is not None:
        assert response.status_code == 200, response.text
    count = len(provider.requests)
    if revoke != 'other_key':
        assert (
            await admin.post(context['endpoint'], headers=context['headers'], json=context['body'])
        ).status_code == status
    assert (await admin.get(context['intent']['polling_url'], headers=context['headers'])).status_code == status
    cancelled = await admin.post(context['intent']['polling_url'] + '/cancel', headers=context['headers'], json={})
    assert cancelled.status_code == status, cancelled.text
    assert 'run' not in cancelled.json() and 'hits' not in cancelled.json()
    assert len(provider.requests) == count


async def pending_public_run(
    client: httpx.AsyncClient, app: FastAPI, intent: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    from uuid import UUID

    from sqlalchemy import select

    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    container = app.state.container
    for _ in range(10):
        await container.knowledge_preparation.tick(UUID(intent['id']))
        intent = (await client.get(intent['polling_url'], headers=headers)).json()
        if intent['run'] is not None:
            assert intent['run']['status'] == 'pending', str(intent)
            return intent
        async with container.database.sessions() as session:
            children = list(
                await session.scalars(
                    select(Run.id).where(Run.status == 'pending', Run.resource_type == 'knowledge_embedding')
                )
            )
        for run_id in children:
            await execute_run(container, run_id)
    raise AssertionError(str(intent))


async def cancel_failed_preparation_after_library_delete(
    admin: httpx.AsyncClient, app: FastAPI, context: dict[str, Any], intent: dict[str, Any]
) -> None:
    from uuid import UUID

    assert (await admin.patch('/api/models/' + context['embedding'], json={'enabled': False})).status_code == 200
    await app.state.container.knowledge_preparation.tick(UUID(intent['id']))
    failed = (await admin.get(intent['polling_url'], headers=context['headers'])).json()
    assert failed['status'] == 'failed', str(failed)
    base_id = context['intent']['hits'][0]['knowledge_base_id']
    base = (await admin.get('/api/knowledge/bases/' + base_id)).json()
    removed = await admin.request(
        'DELETE',
        '/api/knowledge/bases/' + base_id,
        json={'expected_version': base['version'], 'client_request_id': str(uuid4())},
    )
    assert removed.status_code == 202, removed.text


@pytest.mark.parametrize('stage', ['queued', 'waiting_models', 'pending', 'running', 'failed'])
async def test_public_cancel_preserves_authorized_stop_and_terminal_idempotency(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    completed_public_knowledge: dict[str, Any],
    stage: str,
) -> None:
    import asyncio
    from uuid import UUID

    from zhiheng.worker.execution import execute_run

    context = completed_public_knowledge
    if stage == 'running':
        response = await admin.patch('/api/models/' + context['model_id'], json={'upstream_model': 'reasoning-silent'})
        assert response.status_code == 200, response.text
    prepared = await admin.post(
        context['endpoint'], headers=context['headers'], json={**context['body'], 'client_request_id': str(uuid4())}
    )
    assert prepared.status_code == 202, prepared.text
    intent = prepared.json()
    container = app.state.container
    execution = None
    try:
        if stage == 'failed':
            await cancel_failed_preparation_after_library_delete(admin, app, context, intent)
        elif stage == 'waiting_models':
            await container.knowledge_preparation.tick(UUID(intent['id']))
            intent = (await admin.get(intent['polling_url'], headers=context['headers'])).json()
            assert intent['status'] == 'waiting_models', str(intent)
        elif stage in {'pending', 'running'}:
            intent = await pending_public_run(admin, app, intent, context['headers'])
            if stage == 'running':
                execution = asyncio.create_task(execute_run(container, UUID(intent['run']['id'])))
                for _ in range(100):
                    intent = (await admin.get(intent['polling_url'], headers=context['headers'])).json()
                    if intent['run']['reasoning']:
                        break
                    await asyncio.sleep(0.02)
                assert intent['run']['reasoning'] == '先思考', str(intent)
        count = len(provider.requests)
        first = await admin.post(intent['polling_url'] + '/cancel', headers=context['headers'], json={})
        assert first.status_code == 200, first.text
        if execution:
            await asyncio.wait_for(execution, 2)
        repeated = await admin.post(intent['polling_url'] + '/cancel', headers=context['headers'], json={})
        assert repeated.status_code == 200, repeated.text
        if stage in {'pending', 'running'}:
            assert repeated.json()['run']['id'] == intent['run']['id']
            assert repeated.json()['run']['status'] == 'cancelled'
        else:
            assert repeated.json()['status'] == ('failed' if stage == 'failed' else 'cancelled')
        assert len(provider.requests) == count
    finally:
        provider.release.set()
        if execution:
            await asyncio.gather(execution, return_exceptions=True)


@pytest.mark.parametrize('admission', ['admitted', 'ready', 'failed'])
async def test_public_cancel_filters_sources_using_original_owner_acl(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    completed_public_knowledge: dict[str, Any],
    admission: str,
) -> None:
    from unittest.mock import patch
    from uuid import UUID

    from tests.core.integration.test_knowledge import finish_preparation
    from tests.core.integration.test_work import member_client, published

    context = completed_public_knowledge
    hit = context['intent']['hits'][0]
    async with member_client(app) as member:
        member_id = (await member.get('/api/auth/me')).json()['id']
        base_id = hit['knowledge_base_id']
        assert (
            await admin.patch(
                f'/api/knowledge/bases/{base_id}/acl',
                json={'expected_version': 1, 'access': 'public', 'entries': []},
            )
        ).status_code == 200
        file_id = hit['file_id']
        granted = await admin.patch(
            f'/api/files/{file_id}/acl',
            json={'expected_version': 1, 'access': 'private', 'entries': [{'user_id': member_id, 'effect': 'allow'}]},
        )
        assert granted.status_code == 200, granted.text
        agent = await published(member, context['model_id'], knowledge_base_ids=[base_id])
        key = (
            await member.post(
                f'/api/developer/applications/{agent["application_id"]}/keys',
                json={'name': 'Owner ACL', 'model_ids': [context['model_id'], context['embedding']]},
            )
        ).json()
        headers = {'Authorization': 'Bearer ' + key['api_key']}
        response = await member.post(
            f'/v1/agents/{agent["id"]}/chat/completions',
            headers=headers,
            json={**context['body'], 'client_request_id': str(uuid4())},
        )
        assert response.status_code == 202, response.text
        container = app.state.container
        if admission == 'admitted':
            intent = await finish_preparation(member, app, response.json(), headers)
            assert intent['hits'][0]['file_id'] == file_id
        else:

            async def stop_before_admission(*args: Any) -> None:
                if admission == 'failed':
                    raise AppError('test_admission_failed', 'Local admission fault', 409)

            with patch.object(container.knowledge_preparation, 'finalize', stop_before_admission):
                intent = response.json()
                await pending_public_run_until_ready(member, app, intent, headers, admission)
            async with container.database.sessions() as session:
                saved = await container.knowledge_preparation.store.get(session, UUID(intent['id']))
                assert saved.hits[0]['file_id'] == file_id and saved.main_run_id is None
        visible = await member.post(intent['polling_url'] + '/cancel', headers=headers, json={})
        assert visible.status_code == 200, visible.text
        assert visible.json()['hits'] == (intent['hits'] if admission == 'admitted' else [])
        file = (await admin.get('/api/files/' + file_id)).json()
        assert (
            await admin.patch(
                f'/api/files/{file_id}/acl',
                json={'expected_version': file['version'], 'access': 'private', 'entries': []},
            )
        ).status_code == 200
        count = len(provider.requests)
        filtered = await member.post(intent['polling_url'] + '/cancel', headers=headers, json={})
        assert filtered.status_code == 200, filtered.text
        if admission == 'admitted':
            assert filtered.json()['run']['id'] == intent['run']['id']
        else:
            assert filtered.json()['run'] is None
        assert filtered.json()['hits'] == []
        assert len(provider.requests) == count


async def pending_public_run_until_ready(
    client: httpx.AsyncClient,
    app: FastAPI,
    intent: dict[str, Any],
    headers: dict[str, str],
    admission: str,
) -> None:
    from uuid import UUID

    from sqlalchemy import select

    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    container = app.state.container
    for _ in range(10):
        await container.knowledge_preparation.tick(UUID(intent['id']))
        async with container.database.sessions() as session:
            saved = await container.knowledge_preparation.store.get(session, UUID(intent['id']))
            children = list(
                await session.scalars(
                    select(Run.id).where(Run.status == 'pending', Run.resource_type == 'knowledge_embedding')
                )
            )
        if saved.status == admission:
            assert saved.hits
            return
        for run_id in children:
            await execute_run(container, run_id)
    raise AssertionError(saved.status)


async def test_real_single_slot_arq_index_and_retrieval(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    import asyncio

    from sqlalchemy import select

    from tests.core.integration.test_knowledge import embedding_model
    from zhiheng.modules.knowledge.orm import IndexGeneration
    from zhiheng.worker.main import build_worker

    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus URI required')
    container = app.state.container
    vectors = MilvusStore(uri)
    container.knowledge_jobs.vectors = container.knowledge_preparation.vectors = vectors
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Single slot', 'embed_model_id': model})).json()
    file = (
        await admin.post(
            '/api/files',
            params={'filename': 'single.txt'},
            content=b'Single slot durable indexing.',
            headers={'content-type': 'application/octet-stream'},
        )
    ).json()
    job = (
        await admin.post(
            f'/api/knowledge/bases/{base["id"]}/documents',
            json={
                'file_ids': [file['id']],
                'client_request_id': str(uuid4()),
            },
        )
    ).json()
    try:
        for _ in range(2):
            worker = build_worker(container, burst=True, max_jobs=1)
            try:
                await asyncio.wait_for(worker.async_run(), timeout=30)
            finally:
                await worker.close()
            if _ == 0:
                indexed = (await admin.get(f'/api/knowledge/jobs/{job["id"]}')).json()
                assert indexed['status'] == 'completed', str(indexed)
                intent = (
                    await admin.post(
                        f'/api/knowledge/bases/{base["id"]}/query',
                        json={
                            'query': 'single slot',
                            'client_request_id': str(uuid4()),
                        },
                    )
                ).json()
        result = (await admin.get(f'/api/knowledge/preparations/{intent["id"]}')).json()
        assert result['status'] == 'completed', str(result)
        assert result['hits'][0]['file_id'] == file['id']
        assert len(provider.requests) == 2
    finally:
        async with container.database.sessions() as session:
            generations = list(await session.scalars(select(IndexGeneration)))
        for generation in generations:
            if generation.dimension:
                await vectors.drop(
                    VectorSpec(
                        generation.base_id,
                        generation.id,
                        generation.attempt_id,
                        generation.collection_name,
                        generation.fingerprint,
                        generation.dimension,
                    )
                )


async def test_real_milvus_explicit_generation_schema_and_precise_cleanup() -> None:
    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus URI required')
    class RegisteredStore(MilvusStore):
        async def build(self,spec: VectorSpec,rows: list[dict[str,object]],**kwargs: Any) -> None:
            resource('intent','milvus_collection',spec.collection_name,description=spec.description(),database='default')
            await super().build(spec,rows,**kwargs)
            resource('created','milvus_collection',spec.collection_name)
    store = RegisteredStore(uri)
    base, revision, attempt = uuid4(), uuid4(), uuid4()
    spec = VectorSpec(base, revision, attempt, f'zh_k_{base.hex}_{revision.hex}_{attempt.hex}', 'a' * 64, 2)
    document, chunk = uuid4(), uuid4()
    rows = [
        {
            'id': str(chunk),
            'document_id': str(document),
            'content_hash': hashlib.sha256(b'test').hexdigest(),
            'vector': [0.1, 0.2],
        }
    ]
    assert await store.available()
    try:
        await store.build(spec, rows)
        hits = await store.search(spec, [0.1, 0.2], 1, [document])
        assert hits[0]['id'] == str(chunk)
        assert hits[0]['entity']['content_hash'] == rows[0]['content_hash']
    finally:
        await store.drop(spec)


async def test_deleted_generation_tombstone_rechecks_only_its_owned_namespace(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    real_vectors: MilvusStore,
) -> None:
    from datetime import timedelta
    from uuid import UUID

    from sqlalchemy import select

    from tests.core.integration.test_knowledge import embedding_model
    from tests.core.integration.test_knowledge_failures import finish_job, source
    from zhiheng.modules.knowledge.orm import IndexGeneration, KnowledgeChunk
    from zhiheng.shared.time import utcnow

    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Tombstone', 'embed_model_id': model})).json()
    endpoint = f'/api/knowledge/bases/{base["id"]}'
    file = await source(admin, 'late.txt', b'Only this exact registered namespace may be removed.')
    job = await finish_job(
        admin,
        app,
        (
            await admin.post(
                endpoint + '/documents',
                json={
                    'file_ids': [file['id']],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json(),
    )
    container = app.state.container
    old_id = UUID(job['index_revision'])
    async with container.database.sessions() as session:
        generation = await session.get(IndexGeneration, old_id)
        chunks = list(await session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.generation_id == old_id)))
        spec = VectorSpec(
            generation.base_id,
            generation.id,
            generation.attempt_id,
            generation.collection_name,
            generation.fingerprint,
            generation.dimension,
        )
        rows = [
            {
                'id': str(row.id),
                'document_id': str(row.document_id),
                'content_hash': row.content_hash,
                'vector': row.vector,
            }
            for row in chunks
        ]
    current = (await admin.get(endpoint)).json()
    rebuilt = await finish_job(
        admin,
        app,
        (
            await admin.post(
                endpoint + '/rebuild',
                json={
                    'expected_version': current['version'],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json(),
    )
    assert rebuilt['status'] == 'completed'
    async with container.database.sessions.begin() as session:
        generation = await session.get(IndexGeneration, old_id)
        generation.gc_after = utcnow() - timedelta(seconds=1)
    await container.knowledge_jobs.gc(old_id)
    await real_vectors.build(spec, rows)
    async with container.database.sessions.begin() as session:
        generation = await session.get(IndexGeneration, old_id)
        assert generation.status == 'deleted'
        generation.gc_after = utcnow() - timedelta(seconds=1)
    async with container.database.sessions() as session:
        assert old_id in await container.knowledge_jobs.generations.pending(session)
    await container.knowledge_jobs.gc(old_id)
    assert (await admin.get(endpoint)).json()['active_revision'] == rebuilt['index_revision']
    with pytest.raises(AppError):
        await real_vectors.search(spec, rows[0]['vector'], 1, [UUID(rows[0]['document_id'])])
    async with container.database.sessions() as session:
        active = await session.get(IndexGeneration, UUID(rebuilt['index_revision']))
        active_spec = VectorSpec(
            active.base_id, active.id, active.attempt_id, active.collection_name, active.fingerprint, active.dimension
        )
    assert await real_vectors.search(active_spec, rows[0]['vector'], 1, [UUID(rows[0]['document_id'])])
