"""HTTP regressions for confirmed API contract and documentation defects."""

import asyncio
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI

from tests.core.integration.test_developer import provision
from tests.core.integration.test_public_protocol import drive
from tests.core.integration.test_resource_files import auth, post
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.integration.test_resource_knowledge import setup
from tests.core.provider import ProviderServer
from zhiheng.api.inference.responses import CompletionResponse


async def test_cancel_capability_matches_scope_and_resource_actions(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    owned, writer, base = await setup(admin, provider, app)
    uploaded = await post(admin, writer)
    accepted = await admin.post(
        f'/api/v1/knowledge-bases/{base["id"]}/documents',
        headers=auth(writer, uuid4()),
        json={'file_ids': [uploaded.json()['id']]},
    )
    assert accepted.status_code == 202, accepted.text
    endpoint = '/api/v1/operations/' + accepted.json()['id']
    reader = await issue(admin, owned['id'], scopes=['knowledge:read'])
    readonly = await admin.get(endpoint, headers=auth(reader))
    assert readonly.status_code == 200 and readonly.json()['cancel_allowed'] is False
    assert (await admin.post(endpoint + '/cancel', headers=auth(reader))).status_code == 403
    capable = await admin.get(endpoint, headers=auth(writer))
    assert capable.status_code == 200 and capable.json()['cancel_allowed'] is True
    container = app.state.container
    async with container.database.sessions.begin() as session:
        principal = await container.identity.principal_for_user(session, UUID(owned['owner_id']))
        current = await container.applications.require_owned(session, principal.id, UUID(owned['id']))
        await container.resource_access.bind(
            session,
            principal,
            UUID(owned['id']),
            'knowledge_base',
            UUID(base['id']),
            ['read'],
            expected_version=current.version,
        )
    no_write = await admin.get(endpoint, headers=auth(writer))
    assert no_write.status_code == 200 and no_write.json()['cancel_allowed'] is False


async def test_download_example_returns_the_complete_file(admin: httpx.AsyncClient) -> None:
    owned = await application(admin)
    key = await issue(admin, owned['id'], scopes=['files:read', 'files:write'])
    original = b'Complete audit file\n' * 400
    uploaded = await post(admin, key, files={'file': ('whole.txt', original, 'text/plain')})
    assert uploaded.status_code == 201, uploaded.text
    schema = (await admin.get('/openapi/resources-v1.json')).json()
    operation = schema['paths']['/api/v1/files/{file_id}/content']['get']
    example = operation['x-codeSamples'][0]['source']
    assert 'Range:' not in example and '--output' in example
    response = await admin.get('/api/v1/files/' + uploaded.json()['id'] + '/content', headers=auth(key))
    assert response.status_code == 200 and response.content == original
    ranged = await admin.get(
        '/api/v1/files/' + uploaded.json()['id'] + '/content',
        headers={**auth(key), 'Range': 'bytes=0-1023'},
    )
    assert ranged.status_code == 206 and ranged.content == original[:1024]


async def test_inference_success_schemas_keep_json_sse_and_preparation_contracts(admin: httpx.AsyncClient) -> None:
    schema = (await admin.get('/openapi/inference-v1.json')).json()
    for path in ['/v1/chat/completions', '/v1/embeddings', '/v1/rerank', '/v1/agents/{agent_id}/chat/completions']:
        operation = schema['paths'][path]['post']
        response = operation['responses']['200']['content']['application/json']['schema']
        name = response['$ref'].rsplit('/', 1)[-1]
        assert schema['components']['schemas'][name]['properties']
        if 'chat/completions' in path:
            assert 'text/event-stream' in operation['responses']['200']['content']
    agent = schema['paths']['/v1/agents/{agent_id}/chat/completions']['post']
    assert agent['responses']['202']['content']['application/json']['schema']['$ref'].endswith(
        '/AgentPreparationResponse'
    )


async def test_real_json_chat_matches_the_declared_success_schema(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI
) -> None:
    _owned, key = await provision(admin, model_id)
    task = asyncio.create_task(
        admin.post(
            '/v1/chat/completions',
            headers=auth(key),
            json={'model': model_id, 'messages': [{'role': 'user', 'content': 'Contract check'}], 'stream': False},
        )
    )
    response = await drive(app.state.container, task)
    assert response.status_code == 200, response.text
    declared = CompletionResponse.model_validate(response.json())
    assert declared.choices[0].message.content == '你好'
