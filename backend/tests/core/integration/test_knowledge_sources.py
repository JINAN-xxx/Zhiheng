from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_knowledge import finish_preparation
from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.knowledge_support import legacy_grant
from tests.core.legacy_work import legacy_task as task
from tests.core.legacy_work import legacy_turn as turn
from tests.core.legacy_work import legacy_work_request, member_client, published
from tests.core.permission_support import (
    member_agent_work_permissions,  # noqa: F401 - pytest fixture by name
    member_knowledge_permissions,  # noqa: F401 - pytest fixture by name
)
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


async def test_library_acl_filters_sources_and_work_deletion_releases_own_references(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
) -> None:
    base = await ready_base(admin, app, provider, legacy=True)
    endpoint = f'/api/knowledge/bases/{base["id"]}'
    document = (await admin.get(endpoint + '/documents')).json()['items'][0]
    file_id = document['file_id']
    async with member_client(app) as reader:
        reader_id = (await reader.get('/api/auth/me')).json()['id']
        await legacy_grant(app, base['id'], reader_id)
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
        agent = await published(reader, model_id, knowledge_base_ids=[base['id']])
        work = await task(reader, agent, goal='Which port?')
        prepared_work = (
            await legacy_work_request(reader, 'POST', f'/api/work/tasks/{work["id"]}/runs', json=turn(work, agent))
        ).json()
        work_preparation = await finish_preparation(reader, app, prepared_work)
        references = (await admin.get(f'/api/files/{file_id}/references')).json()
        assert {row['resource_id'] for row in references if row['resource_type'] == 'knowledge_run'} == {
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
        detail = (await reader.get(f'/api/work/tasks/{work["id"]}')).json()
        assert all(message['knowledge_sources'] == [] for message in detail['messages'])
        removed = await legacy_work_request(
            reader,
            'DELETE',
            f'/api/work/tasks/{work["id"]}',
            params={'expected_version': detail['version']},
            headers={'content-type': 'application/json'},
        )
        assert removed.status_code == 204, removed.text
        references = (await admin.get(f'/api/files/{file_id}/references')).json()
        assert {row['resource_type'] for row in references} == {'knowledge_document'}


pytestmark = pytest.mark.usefixtures('member_knowledge_permissions', 'member_agent_work_permissions')
