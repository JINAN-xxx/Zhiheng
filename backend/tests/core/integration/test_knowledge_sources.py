from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_knowledge import finish_preparation
from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.integration.test_work import member_client, published, task, turn
from tests.core.provider import ProviderServer
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


async def query_result(client: httpx.AsyncClient, app: FastAPI, base_id: str) -> dict[str, Any]:
    response = await client.post(
        f'/api/knowledge/bases/{base_id}/query',
        json={
            'query': 'Which port?',
            'client_request_id': str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    intent = response.json()
    container = app.state.container
    for _ in range(10):
        await container.knowledge_preparation.tick(UUID(intent['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(container, run_id)
        response = await client.get(f'/api/knowledge/preparations/{intent["id"]}')
        assert response.status_code == 200, response.text
        intent = response.json()
        if intent['status'] in {'completed', 'failed'}:
            break
    assert intent['status'] == 'completed', str(intent)
    return intent


async def test_library_acl_intersects_sources_and_own_history_releases_only_its_references(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
) -> None:
    base = await ready_base(admin, app, provider)
    endpoint = f'/api/knowledge/bases/{base["id"]}'
    document = (await admin.get(endpoint + '/documents')).json()['items'][0]
    file_id = document['file_id']
    await admin.patch(endpoint + '/acl', json={'expected_version': 1, 'access': 'public', 'entries': []})
    async with member_client(app) as reader:
        reader_id = (await reader.get('/api/auth/me')).json()['id']
        before = len(provider.requests)
        empty = await query_result(reader, app, base['id'])
        assert empty['hits'] == [] and len(provider.requests) == before
        assert (await reader.get(endpoint + '/documents')).json()['total'] == 0
        grant = await admin.patch(
            f'/api/files/{file_id}/acl',
            json={
                'expected_version': 1,
                'access': 'private',
                'entries': [{'user_id': reader_id, 'effect': 'allow'}],
            },
        )
        assert grant.status_code == 200, grant.text
        result = await query_result(reader, app, base['id'])
        assert result['hits'][0]['file_id'] == file_id
        conversation = (await reader.post('/api/chat/conversations', json={})).json()
        prepared = (
            await reader.post(
                f'/api/chat/conversations/{conversation["id"]}/runs',
                json={
                    'message': 'Which port?',
                    'model_id': model_id,
                    'knowledge_base_ids': [base['id']],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json()
        chat = await finish_preparation(reader, app, prepared)
        agent = await published(reader, model_id, knowledge_base_ids=[base['id']])
        work = await task(reader, agent, goal='Which port?')
        prepared_work = (await reader.post(f'/api/work/tasks/{work["id"]}/runs', json=turn(work, agent))).json()
        work_preparation = await finish_preparation(reader, app, prepared_work)
        references = (await admin.get(f'/api/files/{file_id}/references')).json()
        assert {row['resource_id'] for row in references if row['resource_type'] == 'knowledge_run'} == {
            chat['run']['id'],
            work_preparation['run']['id'],
        }
        file = (await admin.get(f'/api/files/{file_id}')).json()
        revoked = await admin.patch(
            f'/api/files/{file_id}/acl',
            json={
                'expected_version': file['version'],
                'access': 'private',
                'entries': [],
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert (await reader.get(f'/api/knowledge/preparations/{result["id"]}')).status_code == 404
        messages = (await reader.get(f'/api/chat/conversations/{conversation["id"]}/messages')).json()
        assert all(message['knowledge_sources'] == [] for message in messages)
        detail = (await reader.get(f'/api/work/tasks/{work["id"]}')).json()
        assert all(message['knowledge_sources'] == [] for message in detail['messages'])
        replay = (await reader.get(f'/api/knowledge/preparations/{chat["id"]}')).json()
        assert replay['run']['id'] == chat['run']['id'] and replay['hits'] == []
        version = (await reader.get(f'/api/chat/conversations/{conversation["id"]}')).json()['version']
        removed = await reader.request(
            'DELETE', f'/api/chat/conversations/{conversation["id"]}', json={'expected_version': version}
        )
        assert removed.status_code == 204, removed.text
        references = (await admin.get(f'/api/files/{file_id}/references')).json()
        assert {row['resource_id'] for row in references if row['resource_type'] == 'knowledge_run'} == {
            work_preparation['run']['id']
        }
        removed = await reader.delete(
            f'/api/work/tasks/{work["id"]}',
            params={'expected_version': detail['version']},
            headers={'content-type': 'application/json'},
        )
        assert removed.status_code == 204, removed.text
        references = (await admin.get(f'/api/files/{file_id}/references')).json()
        assert {row['resource_type'] for row in references} == {'knowledge_document'}
