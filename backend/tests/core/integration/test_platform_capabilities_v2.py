"""Use is never maintenance, publishing, credential access or API grant authority."""

from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI

from tests.core.integration.test_platform_module_permissions import grant, member
from tests.core.provider import ProviderServer
from tests.core.unit.test_skill_package import markdown
from zhiheng.modules.identity.admin_orm import IdentityRole
from zhiheng.shared.permission_policy import upgrade_legacy_permissions
from zhiheng.worker.execution import execute_run


async def test_model_directory_cannot_read_credentials_configure_or_test(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    await grant(app, 'models')
    async with member(app) as client:
        visible = await client.get('/api/models')
        assert visible.status_code == 200
        assert all('base_url' not in row and 'api_key' not in row for row in visible.json())
        for path in (
            '/api/models/connections',
            '/api/models/connection-options',
            '/api/models?include_disabled=true',
            '/api/models?trial=true',
        ):
            assert (await client.get(path)).status_code == 403, path
        assert (
            await client.post('/api/models/' + model_id + '/test', json={'client_request_id': str(uuid4())})
        ).status_code == 403
        assert (await client.patch('/api/models/' + model_id, json={'enabled': False})).status_code == 403
        assert (
            await client.post(
                '/api/models/connections',
                json={'name': 'forged', 'base_url': 'https://invalid.example/v1', 'api_key': 'private'},
            )
        ).status_code == 403


async def test_model_configuration_uses_minimal_choices_without_credential_authority(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    await grant(app, 'ai:models:write')
    async with member(app) as client:
        options = await client.get('/api/models/connection-options')
        assert options.status_code == 200 and options.json()
        assert all(set(row) == {'id', 'name', 'enabled'} for row in options.json())
        assert (await client.get('/api/models/connections')).status_code == 403
        connection_id = options.json()[0]['id']
        assert (
            await client.patch('/api/models/connections/' + connection_id, json={'api_key': 'forged'})
        ).status_code == 403
        configured = await client.post(
            '/api/models',
            json={
                'name': 'Allowed configuration',
                'connection_id': connection_id,
                'upstream_model': 'local-text',
                'max_output_tokens': 32,
            },
        )
        assert configured.status_code == 201, configured.text
        assert (
            await client.post('/api/models/' + model_id + '/test', json={'client_request_id': str(uuid4())})
        ).status_code == 403


async def test_credentials_and_connection_test_are_independent(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    credential_role = await grant(app, 'ai:credentials:read')
    async with member(app) as client:
        connections = await client.get('/api/models/connections')
        assert connections.status_code == 200 and connections.json()
        assert 'encrypted_api_key' not in str(connections.json()) and 'local-test-key' not in str(connections.json())
        connection_id = connections.json()[0]['id']
        assert (
            await client.patch('/api/models/connections/' + connection_id, json={'enabled': False})
        ).status_code == 403
        async with app.state.container.database.sessions.begin() as session:
            role = await session.get(IdentityRole, credential_role)
            role.permissions = upgrade_legacy_permissions(['ai:models:test'])
        assert (await client.get('/api/models/connections')).status_code == 403
        test = await client.post('/api/models/' + model_id + '/test', json={'client_request_id': str(uuid4())})
        assert test.status_code == 202, test.text
        async with app.state.container.database.sessions.begin() as session:
            role = await session.get(IdentityRole, credential_role)
            role.permissions = upgrade_legacy_permissions([])
        await execute_run(app.state.container, UUID(test.json()['id']))


async def test_knowledge_use_maintenance_and_sharing_are_three_distinct_grants(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from tests.core.integration.test_knowledge import embedding_model

    embed_id = await embedding_model(admin, provider)
    role_id = await grant(app, 'knowledge')
    async with member(app) as client:
        assert (await client.get('/api/knowledge/bases')).status_code == 200
        payload = {'name': 'Private knowledge', 'embed_model_id': embed_id}
        assert (await client.post('/api/knowledge/bases', json=payload)).status_code == 403
        async with app.state.container.database.sessions.begin() as session:
            role = await session.get(IdentityRole, role_id)
            role.permissions = upgrade_legacy_permissions(['platform:knowledge:manage'])
        base = await client.post('/api/knowledge/bases', json=payload)
        assert base.status_code == 201, base.text
        root = '/api/knowledge/bases/' + base.json()['id']
        assert (await client.get(root + '/invitations')).status_code == 403
        assert (
            await client.post(root + '/query', json={'query': 'hello', 'client_request_id': str(uuid4())})
        ).status_code == 403
        async with app.state.container.database.sessions.begin() as session:
            role = await session.get(IdentityRole, role_id)
            role.permissions = upgrade_legacy_permissions(['platform:knowledge:share'])
        assert (await client.get(root + '/invitations')).status_code == 200
        assert (
            await client.patch(root, json={'name': 'not permitted', 'expected_version': base.json()['version']})
        ).status_code == 403


async def test_assistant_maintenance_does_not_publish_or_formally_use(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    await grant(app, 'agents:manage')
    async with member(app) as client:
        created = await client.post('/api/agents', json={'name': 'Own draft', 'model_id': model_id, 'max_tokens': 32})
        assert created.status_code == 201, created.text
        root = '/api/agents/' + created.json()['id']
        assert (await client.post(root + '/publish', json={'expected_version': 1})).status_code == 403
        assert (await client.post(root + '/conversations', json={'client_request_id': str(uuid4())})).status_code == 403
        await grant(app, 'agents:publish')
        assert (await client.post(root + '/publish', json={'expected_version': 1})).status_code == 200
        assert (await client.post(root + '/conversations', json={'client_request_id': str(uuid4())})).status_code == 403
        await grant(app, 'agents')
        assert (await client.post(root + '/conversations', json={'client_request_id': str(uuid4())})).status_code == 201


async def test_skill_use_can_enable_own_preset_but_cannot_upload_or_delete(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    await grant(app, 'skills')
    async with member(app) as client:
        listing = await client.get('/api/skills')
        assert listing.status_code == 200, listing.text
        preset = next(row for row in listing.json()['items'] if row['source'] == 'builtin')
        enabled = await client.patch(
            '/api/skills/' + preset['id'] + '/status', json={'status': 'enabled', 'expected_version': preset['version']}
        )
        assert enabled.status_code == 200, enabled.text
        assert (
            await client.post(
                '/api/skills/import/prepare?filename=SKILL.md',
                content=markdown('unauthorized-upload', 'private'),
                headers={'content-type': 'application/octet-stream'},
            )
        ).status_code == 403
        assert (
            await client.delete('/api/skills/' + preset['id'], headers={'content-type': 'application/json'})
        ).status_code == 403


async def test_developer_read_manage_and_api_invoke_are_independent(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    await grant(app, 'developer')
    async with member(app) as client:
        assert (await client.get('/api/developer/applications')).status_code == 200
        assert (await client.post('/api/developer/applications', json={'name': 'forged'})).status_code == 403
        await grant(app, 'developer:manage')
        application = (await client.post('/api/developer/applications', json={'name': 'Own SDK app'})).json()
        path = '/api/developer/applications/' + application['id']
        key = await client.post(
            path + '/keys', json={'name': 'Models key', 'scopes': ['models:invoke'], 'model_ids': [model_id]}
        )
        assert key.status_code == 201, key.text
        headers = {'Authorization': 'Bearer ' + key.json()['api_key']}
        assert (await client.get('/v1/models', headers=headers)).status_code == 403
        await grant(app, 'developer:invoke')
        assert (await client.get('/v1/models', headers=headers)).status_code == 200
        for scope in ('knowledge:write', 'skills:write', 'agents:invoke'):
            assert (
                await client.post(path + '/keys', json={'name': 'forged', 'scopes': [scope], 'model_ids': [model_id]})
            ).status_code == 403
        await grant(app, 'knowledge')
        assert (
            await client.post(path + '/keys', json={'name': 'forged', 'scopes': ['knowledge:write'], 'model_ids': []})
        ).status_code == 403


async def test_no_cross_layer_wildcard_and_unknown_permissions_rejected(admin: httpx.AsyncClient) -> None:
    for code in (
        'platform:models:*',
        'platform:credentials:access',
        'platform:knowledge:write',
        'platform:agents:admin',
    ):
        response = await admin.post('/api/admin/roles', json={'name': 'Invalid grant', 'permissions': [code]})
        assert response.status_code in {400, 422}, response.text


async def test_feature_use_does_not_grant_generic_file_acl_transfer_or_purge(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    await grant(app, 'chat', 'knowledge', 'developer')
    async with member(app) as client:
        target = str(uuid4())
        for method, path in (
            ('PATCH', '/api/files/' + target + '/acl'),
            ('POST', '/api/files/' + target + '/transfer'),
            ('POST', '/api/files/' + target + '/purge'),
            ('DELETE', '/api/files/' + target),
        ):
            denied = await client.request(method, path, json={})
            assert denied.status_code == 403, (path, denied.text)
        upload = await client.post(
            '/api/files?filename=source.txt',
            content=b'wrong upload context',
            headers={'content-type': 'application/octet-stream'},
        )
        assert upload.status_code == 403
        chat_upload = await client.post(
            '/api/chat/attachments?filename=note.txt',
            content=b'legitimate chat attachment',
            headers={'content-type': 'application/octet-stream'},
        )
        assert chat_upload.status_code == 201, chat_upload.text
