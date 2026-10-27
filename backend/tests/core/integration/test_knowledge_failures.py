from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_knowledge import VectorFixture, embedding_model
from tests.core.provider import ProviderServer
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


async def source(admin: httpx.AsyncClient, name: str, content: bytes) -> dict[str, Any]:
    response = await admin.post(
        '/api/files', params={'filename': name}, content=content, headers={'content-type': 'application/octet-stream'}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def finish_job(admin: httpx.AsyncClient, app: FastAPI, job: dict[str, Any]) -> dict[str, Any]:
    container = app.state.container
    for _ in range(40):
        await container.knowledge_jobs.tick(UUID(job['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(container, run_id)
        job = (await admin.get(f'/api/knowledge/jobs/{job["id"]}')).json()
        if job['status'] in {'completed', 'partial', 'failed', 'cancelled', 'interrupted'}:
            return job
    raise AssertionError(str(job))


async def ready_base(admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer) -> dict[str, Any]:
    container = app.state.container
    container.knowledge_jobs.vectors = container.knowledge_preparation.vectors = VectorFixture()
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Managed', 'embed_model_id': model})).json()
    file = await source(admin, 'guide.txt', b'The service listens on port 8080.')
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
    return base


async def test_failed_import_preserves_pointer_and_partial_carries_vectors(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    container = app.state.container
    vectors = VectorFixture()
    container.knowledge_jobs.vectors = vectors
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Recovery', 'embed_model_id': model})).json()
    initial = await source(admin, 'old.txt', b'Old active knowledge.')
    bad = await source(admin, 'unsupported.doc', b'Unsupported document.')
    endpoint = f'/api/knowledge/bases/{base["id"]}/documents'
    job = await finish_job(
        admin,
        app,
        (
            await admin.post(
                endpoint,
                json={
                    'file_ids': [initial['id']],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json(),
    )
    assert job['status'] == 'completed'
    active = job['index_revision']
    failed = await finish_job(
        admin,
        app,
        (
            await admin.post(
                endpoint,
                json={
                    'file_ids': [bad['id']],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json(),
    )
    assert failed['status'] == 'failed'
    assert (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()['active_revision'] == active
    good = await source(admin, 'new.txt', b'New knowledge.')
    partial = await finish_job(
        admin,
        app,
        (
            await admin.post(
                endpoint,
                json={
                    'file_ids': [good['id'], bad['id']],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json(),
    )
    assert partial['status'] == 'partial', str(partial)
    assert len(vectors.rows[UUID(partial['index_revision'])]) == 2
    assert len(provider.requests) == 2


async def test_public_preparation_key_scope_polling_replay_and_cancel(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
) -> None:
    from tests.core.integration.test_knowledge import finish_preparation
    from tests.core.integration.test_work import published

    base = await ready_base(admin, app, provider)
    for model in (model_id, base['embed_model_id']):
        response = await admin.post(
            f'/api/models/{model}/rate-cards',
            json={
                'billing_mode': 'token',
                'input_price': '0.000001',
                'output_price': '0.000001',
            },
        )
        assert response.status_code == 201, response.text
    agent = await published(admin, model_id, knowledge_base_ids=[base['id']])
    key = (
        await admin.post(
            f'/api/developer/applications/{agent["application_id"]}/keys',
            json={
                'name': 'Knowledge key',
                'model_ids': [model_id, base['embed_model_id']],
            },
        )
    ).json()
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    body = {'messages': [{'role': 'user', 'content': 'Which port?'}], 'client_request_id': str(uuid4())}
    endpoint = f'/v1/agents/{agent["id"]}/chat/completions'
    response = await admin.post(endpoint, headers=headers, json=body)
    assert response.status_code == 202, response.text
    intent = response.json()
    assert intent['polling_url'] == f'/v1/agents/{agent["id"]}/preparations/{intent["id"]}'
    changed = await admin.post(endpoint, headers=headers, json={**body, 'stream': True})
    assert changed.status_code == 409 and changed.json()['error']['code'] == 'idempotency_conflict'
    assert (await admin.get(f'/api/knowledge/preparations/{intent["id"]}')).status_code == 404
    intent = await finish_preparation(admin, app, intent, headers)
    count = len(provider.requests)
    await admin.patch(f'/api/models/{base["embed_model_id"]}', json={'enabled': False})
    replay = await admin.post(endpoint, headers=headers, json=body)
    assert replay.status_code == 200, replay.text
    assert len(provider.requests) == count
    cancelled = await admin.post(intent['polling_url'] + '/cancel', headers=headers, json={})
    assert cancelled.status_code == 200 and cancelled.json()['run']['id'] == intent['run']['id']
    assert cancelled.json()['status'] == 'admitted'


async def test_work_selection_rechecks_knowledge_availability(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
) -> None:
    from tests.core.integration.test_work import published

    base = await ready_base(admin, app, provider)
    agent = await published(admin, model_id, knowledge_base_ids=[base['id']])
    await admin.patch(f'/api/models/{base["embed_model_id"]}', json={'enabled': False})
    response = await admin.post(
        '/api/work/tasks',
        json={
            'goal': 'Use knowledge',
            'agent_ids': [agent['id']],
            'active_agent_id': agent['id'],
            'client_request_id': str(uuid4()),
        },
    )
    assert response.status_code == 404, response.text


async def test_retrieval_deadline_is_persisted_before_blocking_sdk_drain(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
) -> None:
    import asyncio
    import threading
    from datetime import timedelta

    from zhiheng.modules.knowledge.adapters.milvus import drain_thread
    from zhiheng.modules.knowledge.orm import RetrievalIntent
    from zhiheng.shared.time import utcnow

    base = await ready_base(admin, app, provider)
    container = app.state.container
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    intent = (
        await admin.post(
            f'/api/chat/conversations/{conversation["id"]}/runs',
            json={
                'message': 'Which port?',
                'model_id': model_id,
                'knowledge_base_ids': [base['id']],
                'client_request_id': str(uuid4()),
            },
        )
    ).json()
    await container.knowledge_preparation.tick(UUID(intent['id']))
    async with container.database.sessions() as session:
        pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
    for run_id in pending:
        await execute_run(container, run_id)
    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    class BlockingVectors(VectorFixture):
        async def search(self, *args, **kwargs) -> list[dict[str, Any]]:
            assert kwargs['timeout_seconds'] <= 0.3

            def operation() -> list[dict[str, Any]]:
                started.set()
                try:
                    release.wait(4)
                    return []
                finally:
                    closed.set()

            return await drain_thread(operation)

    container.knowledge_preparation.vectors = BlockingVectors()
    async with container.database.sessions.begin() as session:
        row = await session.get(RetrievalIntent, UUID(intent['id']))
        row.deadline = utcnow() + timedelta(seconds=0.3)
    task = asyncio.create_task(container.knowledge_preparation.tick(UUID(intent['id'])))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.sleep(0.4)
        failed = (await admin.get(f'/api/knowledge/preparations/{intent["id"]}')).json()
        assert failed['status'] == 'failed' and failed['error']['code'] == 'knowledge_timeout', str(failed)
        assert not task.done() and not closed.is_set()
    finally:
        release.set()
        await asyncio.wait_for(task, 2)
    assert closed.is_set()
    async with container.database.sessions() as session:
        assert await session.scalar(select(Run.id).where(Run.resource_type == 'chat')) is None


async def test_rebuild_is_atomic_and_cleanup_does_not_require_live_embedding(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base = await ready_base(admin, app, provider)
    endpoint = f'/api/knowledge/bases/{base["id"]}'
    bad = await source(admin, 'bad.doc', b'unsupported')
    failed = await finish_job(
        admin,
        app,
        (
            await admin.post(
                endpoint + '/documents',
                json={
                    'file_ids': [bad['id']],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json(),
    )
    assert failed['status'] == 'failed'
    previous = (await admin.get(endpoint)).json()
    await admin.patch(f'/api/models/{base["embed_model_id"]}', json={'upstream_model': 'embedding-v2'})
    query = await admin.post(endpoint + '/query', json={'query': 'port', 'client_request_id': str(uuid4())})
    assert query.status_code == 409 and query.json()['error']['code'] == 'knowledge_reindex_required'
    rebuilt = await finish_job(
        admin,
        app,
        (
            await admin.post(
                endpoint + '/rebuild',
                json={
                    'expected_version': previous['version'],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json(),
    )
    assert rebuilt['status'] == 'failed', str(rebuilt)
    assert (await admin.get(endpoint)).json()['active_revision'] == previous['active_revision']
    await admin.patch(f'/api/models/{base["embed_model_id"]}', json={'enabled': False})
    document = next(
        item for item in (await admin.get(endpoint + '/documents')).json()['items'] if item['file_id'] == bad['id']
    )
    count = len(provider.requests)
    removed = await admin.request(
        'DELETE',
        endpoint + '/documents/' + document['id'],
        json={
            'expected_version': document['version'],
            'client_request_id': str(uuid4()),
        },
    )
    assert removed.status_code == 202, removed.text
    removed = await finish_job(admin, app, removed.json())
    assert removed['status'] == 'completed', str(removed)
    assert len(provider.requests) == count
    await admin.patch(f'/api/models/{base["embed_model_id"]}', json={'enabled': True})
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
    assert rebuilt['status'] == 'completed', str(rebuilt)


async def test_concurrent_prepare_and_rejected_final_admission_keep_one_paid_child(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
) -> None:
    import asyncio

    base = await ready_base(admin, app, provider)
    container = app.state.container
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    endpoint = f'/api/chat/conversations/{conversation["id"]}/runs'
    body = {
        'message': 'Which port?',
        'model_id': model_id,
        'knowledge_base_ids': [base['id']],
        'client_request_id': str(uuid4()),
    }
    replies = await asyncio.gather(admin.post(endpoint, json=body), admin.post(endpoint, json=body))
    assert [reply.status_code for reply in replies] == [202, 202]
    intents = [reply.json() for reply in replies]
    assert intents[0]['id'] == intents[1]['id']
    intent_id = UUID(intents[0]['id'])
    await container.knowledge_preparation.tick(intent_id)
    async with container.database.sessions() as session:
        children = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
    assert len(children) == 1
    await execute_run(container, children[0])
    document = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items'][0]
    changed = await admin.patch(
        f'/api/files/{document["file_id"]}', json={'expected_version': 1, 'display_name': 'Changed source'}
    )
    assert changed.status_code == 200, changed.text
    await container.knowledge_preparation.tick(intent_id)
    failed = (await admin.get(f'/api/knowledge/preparations/{intent_id}')).json()
    assert failed['status'] == 'failed' and failed['run'] is None
    assert failed['error']['code'] == 'knowledge_preparation_stale', str(failed)
    replay = await admin.post(endpoint, json=body)
    assert replay.status_code == 202 and replay.json()['id'] == str(intent_id)
    assert replay.json()['status'] == 'failed'
    assert len(provider.requests) == 2
    async with container.database.sessions() as session:
        assert await session.scalar(select(Run.id).where(Run.resource_type == 'chat')) is None


async def test_failed_rechunk_keeps_old_active_document_and_real_preview(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base = await ready_base(admin, app, provider)
    endpoint = f'/api/knowledge/bases/{base["id"]}'
    document = (await admin.get(endpoint + '/documents')).json()['items'][0]
    previous = (await admin.get(endpoint)).json()['active_revision']
    provider.embedding_zero = True
    job = (
        await admin.post(
            endpoint + '/documents/' + document['id'] + '/rechunk',
            json={
                'expected_version': document['version'],
                'chunk_size': 500,
                'chunk_overlap': 100,
                'client_request_id': str(uuid4()),
            },
        )
    ).json()
    failed = await finish_job(admin, app, job)
    assert failed['status'] == 'failed', str(failed)
    current = (await admin.get(endpoint + '/documents')).json()['items'][0]
    assert current['status'] == 'failed' and current['active_revision'] == document['active_revision']
    assert (await admin.get(endpoint)).json()['active_revision'] == previous
    preview = await admin.get(endpoint + '/documents/' + document['id'] + '/preview')
    assert preview.status_code == 200 and preview.json()['text'] == 'The service listens on port 8080.'
    assert preview.json()['chunk_count'] == 1
    provider.embedding_zero = False
