"""Personal resources do not inherit an administrator's configuration authority."""

from datetime import timedelta
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI

from tests.core.integration.test_agents import config
from tests.core.integration.test_work import published, task
from zhiheng.modules.files.orm import FileRecord
from zhiheng.modules.knowledge.orm import RetrievalIntent
from zhiheng.modules.models.public import ModelMessage, ModelRequest
from zhiheng.runtime.contracts import ClaimedRun
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

PASSWORD = 'platform-test-password'


async def owner_client(app: FastAPI) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
    assert (await client.post('/api/auth/login', json={'username': 'member', 'password': PASSWORD})).status_code == 200
    return client


async def upload(client: httpx.AsyncClient) -> dict:
    response = await client.post(
        '/api/files',
        params={'filename': 'PRIVATE_document.txt'},
        content=b'PRIVATE_CONTENT',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_admin_configuration_rights_do_not_enumerate_or_mutate_private_resources(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    owner = await owner_client(app)
    try:
        agent = await published(owner, model_id, name='PRIVATE_AGENT', system_prompt='PRIVATE_PROMPT')
        work = await task(owner, agent, goal='PRIVATE_GOAL')
        app_id = agent['application_id']
        application = (await owner.get('/api/developer/applications/' + app_id)).json()
        key = await owner.post(
            f'/api/developer/applications/{app_id}/keys', json={'name': 'PRIVATE_KEY', 'model_ids': [model_id]}
        )
        assert key.status_code == 201, key.text
        file = await upload(owner)
        collections = [
            ('/api/agents', {'search': 'PRIVATE'}),
            ('/api/work/tasks', {'search': 'PRIVATE'}),
            ('/api/developer/applications', {'search': application['name']}),
            ('/api/files', {'search': 'PRIVATE'}),
            ('/api/files', {'owner_id': file['owner_id']}),
        ]
        for endpoint, params in collections:
            response = await admin.get(endpoint, params=params)
            assert response.status_code == 200, response.text
            assert response.json()['items'] == [], response.text
            assert 'PRIVATE' not in response.text
        paths = [
            f'/api/agents/{agent["id"]}',
            f'/api/work/tasks/{work["id"]}',
            f'/api/developer/applications/{app_id}',
            f'/api/developer/applications/{app_id}/keys',
            f'/api/developer/applications/{app_id}/resources',
            f'/api/files/{file["id"]}',
            f'/api/files/{file["id"]}/content',
            f'/api/files/{file["id"]}/download',
            f'/api/files/{file["id"]}/acl',
            f'/api/files/{file["id"]}/audit',
        ]
        for path in paths:
            response = await admin.get(path)
            assert response.status_code == 404, (path, response.status_code, response.text)
            assert 'PRIVATE' not in response.text
        mutations = [
            ('PATCH', f'/api/agents/{agent["id"]}', config(model_id, expected_version=agent['version'])),
            (
                'POST',
                f'/api/agents/{agent["id"]}/preview',
                {'messages': [{'role': 'user', 'content': 'attempt'}], 'client_request_id': str(uuid4())},
            ),
            ('PATCH', f'/api/work/tasks/{work["id"]}', {'title': 'attempt', 'expected_version': work['version']}),
            (
                'PATCH',
                f'/api/developer/applications/{app_id}',
                {'name': 'attempt', 'expected_version': application['version']},
            ),
            ('POST', f'/api/developer/applications/{app_id}/keys', {'name': 'attempt', 'model_ids': [model_id]}),
            ('PATCH', f'/api/files/{file["id"]}', {'display_name': 'attempt', 'expected_version': file['version']}),
            (
                'POST',
                f'/api/files/{file["id"]}/transfer',
                {
                    'expected_version': file['version'],
                    'owner_id': (await admin.get('/api/auth/me')).json()['id'],
                    'uploader_access_enabled': False,
                    'reason': 'attempt',
                },
            ),
        ]
        for verb, path, body in mutations:
            response = await admin.request(verb, path, json=body)
            assert response.status_code == 404, (verb, path, response.status_code, response.text)
        assert (await owner.get(f'/api/agents/{agent["id"]}')).json()['system_prompt'] == 'PRIVATE_PROMPT'
        assert (await owner.get(f'/api/work/tasks/{work["id"]}')).json()['goal'] == 'PRIVATE_GOAL'
        assert (await owner.get(f'/api/files/{file["id"]}/download')).content == b'PRIVATE_CONTENT'
        denied = await admin.request('DELETE', f'/api/models/{model_id}', json={})
        assert denied.status_code == 409, denied.text
        assert 'PRIVATE' not in denied.text and agent['id'] not in denied.text
        assert all(ref['id'] is None and ref['path'] is None for ref in denied.json()['error']['details']['references'])
        for endpoint in (
            '/api/admin/users',
            '/api/admin/configs',
            '/api/admin/monitor',
            '/api/files/policies',
            '/api/usage',
        ):
            response = await admin.get(endpoint)
            assert response.status_code == 200, (endpoint, response.text)
    finally:
        await owner.aclose()


async def test_explicit_file_share_allows_admin_read_without_management_authority(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    owner = await owner_client(app)
    try:
        file = await upload(owner)
        path = f'/api/files/{file["id"]}'
        admin_id = (await admin.get('/api/auth/me')).json()['id']
        acl = (await owner.get(path + '/acl')).json()
        assert not any(p['source'] == 'admin' for p in acl['builtin_permissions'])
        parsed = await owner.post(path + '/parse', json={})
        assert parsed.status_code == 200, parsed.text
        artifact = '/api/files/' + parsed.json()['artifact_file_id']
        assert (await admin.patch(artifact, json={'expected_version': 1, 'display_name': 'attempt'})).status_code == 404
        shared = await owner.patch(
            path + '/acl',
            json={
                'expected_version': file['acl_version'],
                'access': 'private',
                'entries': [{'user_id': admin_id, 'effect': 'allow'}],
            },
        )
        assert shared.status_code == 200, shared.text
        assert (await admin.get(path + '/download')).content == b'PRIVATE_CONTENT'
        assert (await admin.get('/api/files', params={'search': 'PRIVATE'})).json()['total'] == 1
        assert (await admin.get(path + '/acl')).status_code == 404
        fresh = (await owner.get(path)).json()
        response = await admin.patch(path, json={'display_name': 'not owner', 'expected_version': fresh['version']})
        assert response.status_code == 404
        revoked = await owner.patch(
            path + '/acl', json={'expected_version': fresh['acl_version'], 'access': 'private', 'entries': []}
        )
        assert revoked.status_code == 200, revoked.text
        assert (await admin.get(path + '/download')).status_code == 404
        assert (await admin.get('/api/files', params={'search': 'PRIVATE'})).json()['total'] == 0
    finally:
        await owner.aclose()


async def test_historical_admin_actor_loses_foreign_resource_snapshot_cancel_and_execution(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    owner = await owner_client(app)
    try:
        agent = await published(owner, model_id, name='PRIVATE_LEGACY')
        work = await task(owner, agent, goal='PRIVATE legacy task')
        admin_id = UUID((await admin.get('/api/auth/me')).json()['id'])
        container = app.state.container
        for kind, resource_id in [('agent_preview', agent['id']), ('work_turn', work['id'])]:
            async with container.database.sessions.begin() as session:
                binding = await container.models.resolve(session, UUID(model_id))
                request = ModelRequest(binding=binding, messages=(ModelMessage('user', 'PRIVATE frozen context'),))
                run = Run(
                    owner_id=admin_id,
                    billing_owner_id=UUID(agent['owner_id']),
                    resource_type=kind,
                    resource_id=UUID(resource_id),
                    client_request_id=uuid4(),
                    fingerprint='old-admin-actor',
                    payload=request.model_dump(mode='json'),
                )
                session.add(run)
                await session.flush()
                run_id = run.id
                preparation = RetrievalIntent(
                    namespace_id=admin_id,
                    client_request_id=uuid4(),
                    actor_id=admin_id,
                    owner_id=UUID(agent['owner_id']),
                    resource_type=kind,
                    resource_id=run.resource_id,
                    fingerprint='old-admin-actor',
                    request={},
                    dependencies={},
                    main_run_id=run_id,
                    deadline=utcnow() + timedelta(minutes=1),
                )
                session.add(preparation)
                await session.flush()
                preparation_id = preparation.id
                claim = ClaimedRun(
                    run.id, kind, run.resource_id, admin_id, uuid4(), run.payload, UUID(agent['owner_id'])
                )
            preparation_path = f'/api/knowledge/preparations/{preparation_id}'
            assert (await admin.get(preparation_path)).status_code == 404
            assert (await admin.post(preparation_path + '/cancel', json={})).status_code == 404
            for suffix in ('', '/events'):
                response = await admin.get(f'/api/runs/{run_id}' + suffix)
                assert response.status_code == 404, response.text
            assert (await admin.post(f'/api/runs/{run_id}/cancel', json={})).status_code == 404
            try:
                await container.execution_authorization.require(claim)
            except AppError as error:
                assert error.status_code == 404
            else:
                raise AssertionError('Foreign content reached generation authorization')
            if kind == 'work_turn':
                assert (await owner.get(f'/api/work/tasks/{resource_id}/runs/{run_id}')).status_code == 200
    finally:
        await owner.aclose()


async def test_platform_maintenance_purges_expired_foreign_files_without_granting_content_access(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    owner = await owner_client(app)
    try:
        file = await upload(owner)
        identifier = UUID(file['id'])
        async with app.state.container.database.sessions.begin() as session:
            row = await session.get(FileRecord, identifier)
            row.expires_at = utcnow() - timedelta(days=1)
        assert (await admin.get(f'/api/files/{identifier}')).status_code == 404
        expiry = await admin.post('/api/files/jobs/expiry', json={})
        assert expiry.status_code == 200 and expiry.json()['status'] == 'succeeded', expiry.text
        async with app.state.container.database.sessions.begin() as session:
            row = await session.get(FileRecord, identifier)
            assert row.status == 'deleted'
            row.retention_until = utcnow() - timedelta(days=1)
        purge = await admin.post('/api/files/jobs/purge', json={})
        assert purge.status_code == 200 and purge.json()['status'] == 'succeeded', purge.text
        assert 'PRIVATE' not in purge.text
        async with app.state.container.database.sessions() as session:
            assert (await session.get(FileRecord, identifier)).status == 'purged'
        assert (await admin.get(f'/api/files/{identifier}/download')).status_code == 404
    finally:
        await owner.aclose()
