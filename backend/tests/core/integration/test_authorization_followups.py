"""Additional authorization contracts found while building the per-operation matrix."""

import httpx
import pytest
from fastapi import FastAPI

from tests.core.auth_personal_support import identity_client, ids
from tests.core.integration.test_agents import config
from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.integration.test_knowledge_sources import query_result
from tests.core.knowledge_support import remove_library_member, share_library
from tests.core.provider import ProviderServer


@pytest.mark.parametrize(
    'path',
    [
        '/api/v1/knowledge-bases/{base_id}/rebuild',
        '/api/v1/knowledge-bases/{base_id}/documents/{document_id}/rechunk',
    ],
)
async def test_reprocessing_documents_files_read_capability(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get('/openapi/resources-v1.json')
    assert response.status_code == 200
    operation = response.json()['paths'][path]['post']
    assert operation['x-required-scopes'] == ['knowledge:write']


async def test_actor_can_stop_own_query_after_base_revocation_without_receiving_old_hits(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    documents = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items']
    file_id = documents[0]['file_id']
    file = (await admin.get(f'/api/files/{file_id}')).json()
    public = await admin.patch(
        f'/api/files/{file_id}/acl',
        json={'expected_version': file['version'], 'access': 'public', 'entries': []},
    )
    assert public.status_code == 200, public.text
    async with identity_client(app) as reader:
        (await reader.get('/api/auth/me')).json()['id']
        await share_library(admin, reader, base['id'])
        intent = await query_result(reader, app, base['id'])
        assert intent['status'] == 'completed' and intent['hits']
        await remove_library_member(admin, reader, base['id'])
        path = f'/api/knowledge/preparations/{intent["id"]}'
        assert (await reader.get(path)).status_code == 404
        cancelled = await reader.post(path + '/cancel', json={})
        assert cancelled.status_code == 200 and cancelled.json()['hits'] == [], cancelled.text


@pytest.mark.parametrize('actor_admin', [False, True], ids=['member', 'other-admin'])
async def test_agent_source_selector_requires_agent_management_authority(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str, actor_admin: bool
) -> None:
    base = await ready_base(admin, app, provider)
    created = await admin.post('/api/agents', json=config(model_id))
    assert created.status_code == 201, created.text
    endpoint = '/api/knowledge/available'
    selector = {'agent_id': created.json()['id']}
    assert base['id'] in ids((await admin.get(endpoint, params=selector)).json())
    async with identity_client(app, admin=actor_admin) as actor:
        assert base['id'] not in ids((await actor.get(endpoint)).json())
        selected = await actor.get(endpoint, params=selector)
        assert selected.status_code == 404, selected.text
        assert (await actor.get(f'/api/knowledge/bases/{base["id"]}')).status_code == 404
