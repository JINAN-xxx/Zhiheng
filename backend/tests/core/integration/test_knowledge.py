from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI

from tests.core.provider import ProviderServer
from zhiheng.modules.knowledge.public import VectorSpec


class VectorFixture:
    def __init__(self) -> None:
        self.rows = {}

    async def build(self, spec: VectorSpec, rows: list[dict[str, Any]], **kwargs: Any) -> None:
        self.rows[spec.revision] = rows

    async def drop(self, spec: VectorSpec) -> None:
        self.rows.pop(spec.revision, None)

    async def search(
        self, spec: VectorSpec, vector: list[float], limit: int, document_ids: list[UUID], *, timeout_seconds: float = 8
    ) -> list[dict[str, Any]]:
        return [
            {'id': row['id'], 'distance': 0.9, 'entity': row}
            for row in self.rows[spec.revision]
            if row['document_id'] in {str(value) for value in document_ids}
        ][:limit]

    async def hybrid_search(
        self,
        spec: VectorSpec,
        vector: list[float],
        query: str,
        limit: int,
        document_ids: list[UUID],
        *,
        timeout_seconds: float = 8,
    ) -> list[dict[str, Any]]:
        return await self.search(spec, vector, limit, document_ids, timeout_seconds=timeout_seconds)


async def embedding_model(admin: httpx.AsyncClient, provider: ProviderServer) -> str:
    connection = (
        await admin.post(
            '/api/models/connections',
            json={
                'name': 'Knowledge embedding',
                'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
                'api_key': 'local-test-key',
            },
        )
    ).json()
    response = await admin.post(
        '/api/models',
        json={
            'name': 'Knowledge embedding',
            'connection_id': connection['id'],
            'upstream_model': 'embedding',
            'max_output_tokens': 64,
            'model_type': 'embedding',
        },
    )
    assert response.status_code == 201, response.text
    await configured_ranker(admin, provider, connection_id=connection['id'])
    return response.json()['id']


async def configured_ranker(
    admin: httpx.AsyncClient, provider: ProviderServer, *, connection_id: str | None = None
) -> str:
    current = (await admin.get('/api/knowledge/settings')).json()
    if current.get('default_rerank_model_id'):
        return current['default_rerank_model_id']
    if connection_id is None:
        connection = await admin.post(
            '/api/models/connections',
            json={
                'name': 'Knowledge rerank',
                'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
                'api_key': 'local-test-key',
            },
        )
        assert connection.status_code == 201, connection.text
        connection_id = connection.json()['id']
    created = await admin.post(
        '/api/models',
        json={
            'name': 'Required knowledge rerank',
            'connection_id': connection_id,
            'upstream_model': 'rerank',
            'model_type': 'rerank',
            'max_output_tokens': 64,
        },
    )
    assert created.status_code == 201, created.text
    saved = await admin.patch(
        '/api/knowledge/settings',
        json={'expected_version': current['version'], 'default_rerank_model_id': created.json()['id']},
    )
    assert saved.status_code == 200, saved.text
    return created.json()['id']


async def test_base_folder_acl_upload_and_request_identity(admin: httpx.AsyncClient, provider: ProviderServer) -> None:
    model = await embedding_model(admin, provider)
    created = await admin.post('/api/knowledge/bases', json={'name': 'Manual', 'embed_model_id': model})
    assert created.status_code == 201, created.text
    base = created.json()
    assert base['index_status'] == 'empty' and base['access'] == 'private'
    assert base['active_revision'] is None and base['can_manage']
    folder = await admin.post(f'/api/knowledge/bases/{base["id"]}/folders', json={'name': 'Guides'})
    assert folder.status_code == 201, folder.text
    uploaded = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/uploads',
        params={
            'filename': 'guide.txt',
            'folder_id': folder.json()['id'],
        },
        content=b'The service listens on port 8080.',
        headers={'content-type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()['upload_id'] != uploaded.json()['file']['id']
    assert uploaded.json()['file']['access'] == 'private'
    request = {
        'file_ids': [uploaded.json()['file']['id']],
        'folder_id': folder.json()['id'],
        'client_request_id': str(uuid4()),
    }
    job = await admin.post(f'/api/knowledge/bases/{base["id"]}/documents', json=request)
    assert job.status_code == 202, job.text
    repeated = await admin.post(f'/api/knowledge/bases/{base["id"]}/documents', json=request)
    assert repeated.json()['id'] == job.json()['id']
    conflict = await admin.post(f'/api/knowledge/bases/{base["id"]}/documents', json={**request, 'chunk_size': 500})
    assert conflict.status_code == 409
    assert (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['total'] == 1


async def test_knowledge_rejects_chat_model_and_unimplemented_graph(admin: httpx.AsyncClient, model_id: str) -> None:
    response = await admin.post('/api/knowledge/bases', json={'name': 'Wrong', 'embed_model_id': model_id})
    assert response.status_code == 422
    response = await admin.get('/api/knowledge/capabilities')
    assert response.status_code == 200
    assert response.json()['graph_available'] is False
    assert response.json()['native_formats'] == ['pdf', 'docx', 'xlsx', 'txt', 'md', 'csv', 'json', 'html', 'htm']


async def test_index_tick_returns_for_children_and_publishes_complete_generation(
    admin: httpx.AsyncClient, provider: ProviderServer, app: FastAPI
) -> None:
    from uuid import UUID

    from sqlalchemy import select

    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    container = app.state.container
    container.knowledge_jobs.vectors = VectorFixture()
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Manual', 'embed_model_id': model})).json()
    file = (
        await admin.post(
            '/api/files',
            params={'filename': 'guide.txt'},
            content=b'The service listens on port 8080.',
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
    for _ in range(20):
        await container.knowledge_jobs.tick(UUID(job['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(container, run_id)
        job = (await admin.get(f'/api/knowledge/jobs/{job["id"]}')).json()
        if job['status'] in {'completed', 'failed', 'partial'}:
            break
    assert job['status'] == 'completed', str(job)
    base = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
    assert base['active_revision'] == job['index_revision']
    assert job['completed_documents'] == 1 and job['chunk_count'] == 1
    assert len(provider.requests) == 1
    usage = (await admin.get('/api/usage')).json()
    assert len(usage['records']) == 1 and usage['records'][0]['source'] == 'knowledge_embedding'
    query = {'query': 'Which port?', 'client_request_id': str(uuid4())}
    response = await admin.post(f'/api/knowledge/bases/{base["id"]}/query', json=query)
    assert response.status_code == 202, response.text
    preparation = response.json()
    assert preparation['kind'] == 'knowledge_preparation'
    container.knowledge_preparation.vectors = container.knowledge_jobs.vectors
    for _ in range(10):
        await container.knowledge_preparation.tick(UUID(preparation['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(container, run_id)
        preparation = (await admin.get(f'/api/knowledge/preparations/{preparation["id"]}')).json()
        if preparation['status'] in {'completed', 'failed'}:
            break
    assert preparation['status'] == 'completed', str(preparation)
    assert preparation['hits'][0]['content'] == 'The service listens on port 8080.'
    repeated = await admin.post(f'/api/knowledge/bases/{base["id"]}/query', json=query)
    assert repeated.json()['id'] == preparation['id']
    assert len(provider.requests) == 3
    assert provider.requests[-1]['model'] == 'rerank'
    await assert_chat_preparation(admin, provider, app, base, preparation)
    await assert_agent_work_preparation(admin, provider, app, base, chat_model_id=None)


async def assert_chat_preparation(
    admin: httpx.AsyncClient, provider: ProviderServer, app: FastAPI, base: dict, preparation: dict
) -> None:
    from sqlalchemy import select

    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    container = app.state.container
    chat_model = await __import__('tests.core.integration.test_worker', fromlist=['named_model']).named_model(
        admin, provider, 'local-text'
    )
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    body = {
        'message': 'Which port?',
        'model_id': chat_model,
        'knowledge_base_ids': [base['id']],
        'client_request_id': str(uuid4()),
    }
    prepared = await admin.post(f'/api/chat/conversations/{conversation["id"]}/runs', json=body)
    assert prepared.status_code == 202, prepared.text
    intent = prepared.json()
    assert intent['kind'] == 'knowledge_preparation'
    restored = (await admin.get(f'/api/chat/conversations/{conversation["id"]}')).json()
    assert restored['preparation_id'] == intent['id']
    for _ in range(10):
        await container.knowledge_preparation.tick(UUID(intent['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(container, run_id)
        intent = (await admin.get(f'/api/knowledge/preparations/{intent["id"]}')).json()
        if intent['status'] in {'admitted', 'failed'}:
            break
    assert intent['status'] == 'admitted', str(intent)
    replay = await admin.post(f'/api/chat/conversations/{conversation["id"]}/runs', json=body)
    assert replay.json()['id'] == intent['run']['id']
    messages = (await admin.get(f'/api/chat/conversations/{conversation["id"]}/messages')).json()
    assert messages[0]['knowledge_sources'][0]['document_id'] == preparation['hits'][0]['document_id']


async def finish_preparation(
    admin: httpx.AsyncClient, app: FastAPI, preparation: dict, headers: dict[str, str] | None = None
) -> dict:
    from sqlalchemy import select

    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    container = app.state.container
    url = preparation.get('polling_url') or f'/api/knowledge/preparations/{preparation["id"]}'
    for _ in range(10):
        await container.knowledge_preparation.tick(UUID(preparation['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(container, run_id)
        response = await admin.get(url, headers=headers)
        assert response.status_code == 200, response.text
        preparation = response.json()
        if preparation['status'] in {'admitted', 'failed', 'cancelled'}:
            break
    assert preparation['status'] == 'admitted', str(preparation)
    return preparation


async def assert_agent_work_preparation(
    admin: httpx.AsyncClient, provider: ProviderServer, app: FastAPI, base: dict, chat_model_id: str | None
) -> None:
    from tests.core.integration.test_work import published, task, turn
    from tests.core.integration.test_worker import named_model

    chat_model = chat_model_id or await named_model(admin, provider, 'knowledge-chat')
    agent = await published(admin, chat_model, knowledge_base_ids=[base['id']])
    preview = await admin.post(
        f'/api/agents/{agent["id"]}/preview',
        json={
            'messages': [{'role': 'user', 'content': 'Which port?'}],
            'client_request_id': str(uuid4()),
        },
    )
    assert preview.status_code == 202, preview.text
    assert preview.json()['kind'] == 'knowledge_preparation'
    await finish_preparation(admin, app, preview.json())
    work = await task(admin, agent, goal='Which port?')
    pending = await admin.post(f'/api/work/tasks/{work["id"]}/runs', json=turn(work, agent))
    assert pending.status_code == 202, pending.text
    assert pending.json()['kind'] == 'knowledge_preparation'
    restored = (await admin.get(f'/api/work/tasks/{work["id"]}')).json()
    assert restored['version'] > work['version'] and restored['preparation_id'] == pending.json()['id']
    await finish_preparation(admin, app, pending.json())
    done = (await admin.get(f'/api/work/tasks/{work["id"]}')).json()
    assert done['preparation_id'] is None and done['messages'][-1]['knowledge_sources']
