"""Module switches are explicit, apply to direct requests and cannot widen API identities."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.provider import ProviderServer
from zhiheng.modules.identity.admin_orm import IdentityRole, IdentityUserRole
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.shared.permission_policy import upgrade_legacy_permissions
from zhiheng.shared.platform_permissions import PLATFORM_PERMISSION_CODES
from zhiheng.worker.execution import execute_run


async def grant(app: FastAPI, *modules: str) -> UUID:
    async with app.state.container.database.sessions.begin() as session:
        user = await session.scalar(select(IdentityUser).where(IdentityUser.username == 'member'))
        assert user is not None
        role = IdentityRole(
            code='module_' + uuid4().hex,
            name='Configured modules',
            permissions=upgrade_legacy_permissions(
                [
                    m
                    if m.startswith(('platform:', 'ai:', 'system:'))
                    else ('platform:' + m if ':' in m else f'platform:{m}:access')
                    for m in modules
                ]
            ),
            is_active=True,
        )
        session.add(role)
        await session.flush()
        session.add(IdentityUserRole(user_id=user.id, role_id=role.id))
        return role.id


@asynccontextmanager
async def member(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(
            '/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'}
        )
        assert response.status_code == 200
        yield client


async def test_catalog_has_one_switch_per_module_and_no_automatic_grants(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    catalog = (await admin.get('/api/admin/permissions')).json()['items']
    codes = {item['code'] for item in catalog if item['code'].startswith('platform:')}
    assert codes == PLATFORM_PERMISSION_CODES
    assert len([code for code in codes if code.endswith(':enabled')]) == 9
    role = await admin.post('/api/admin/roles', json={'name': 'Empty role'})
    assert role.status_code == 201 and role.json()['permissions'] == []
    async with member(app) as client:
        me = (await client.get('/api/auth/me')).json()
        assert me['permissions'] == []
        for path in (
            '/api/chat/conversations',
            '/api/agents',
            '/api/work/tasks',
            '/api/knowledge/bases',
            '/api/skills',
            '/api/developer/applications',
            '/api/usage',
            '/api/models',
        ):
            denied = await client.get(path)
            assert denied.status_code == 403, (path, denied.text)
        assert (await client.get('/api/auth/profile')).status_code == 200


async def test_modules_combine_across_roles_and_role_disable_revokes_immediately(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    chat_role = await grant(app, 'chat')
    await grant(app, 'skills')
    async with member(app) as client:
        assert (await client.get('/api/chat/conversations')).status_code == 200
        assert (await client.get('/api/skills')).status_code == 200
        assert (
            await client.get('/api/models')
        ).status_code == 200  # Selector works without the model-management module.
        assert (await client.get('/api/models/connections')).status_code == 403
        assert (await client.get('/api/knowledge/bases')).status_code == 403
        user_id = (await client.get('/api/auth/me')).json()['id']
        effective = (await admin.get('/api/admin/users/' + user_id + '/effective-permissions')).json()
        assert {item['code'] for item in effective['items']} == set(
            upgrade_legacy_permissions(['platform:chat:access', 'platform:skills:access'])
        )
        assert all(item['roles'] == ['Configured modules'] for item in effective['items'])
        assert (await client.get('/api/admin/users/' + user_id + '/effective-permissions')).status_code == 403
        current = (await admin.get('/api/admin/roles/' + str(chat_role))).json()
        changed = await admin.patch(
            '/api/admin/roles/' + str(chat_role), json={'is_active': False, 'expected_version': current['version']}
        )
        assert changed.status_code == 200, changed.text
        assert (await client.get('/api/chat/conversations')).status_code == 403
        assert (await client.get('/api/skills')).status_code == 200


async def test_revoked_module_blocks_queued_execution_and_run_reads(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    role_id = await grant(app, 'chat')
    async with member(app) as client:
        conversation = (await client.post('/api/chat/conversations', json={})).json()
        response = await client.post(
            f'/api/chat/conversations/{conversation["id"]}/runs',
            json={'model_id': model_id, 'message': 'private', 'client_request_id': str(uuid4())},
        )
        assert response.status_code == 202, response.text
        run = response.json()
        async with app.state.container.database.sessions.begin() as session:
            role = await session.get(IdentityRole, role_id)
            role.permissions = []
        await execute_run(app.state.container, UUID(run['id']))
        assert not provider.requests
        assert (await client.get('/api/runs/' + run['id'])).status_code == 403
        async with app.state.container.database.sessions() as session:
            snapshot = await app.state.container.runs.snapshot(
                session, UUID(run['id']), UUID((await client.get('/api/auth/me')).json()['id'])
            )
            assert snapshot.status == 'failed' and snapshot.error.code == 'forbidden'


async def test_developer_switch_never_grants_other_users_apps_keys_or_governance(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    application = (await admin.post('/api/developer/applications', json={'name': 'Admin application'})).json()
    key = (
        await admin.post(
            f'/api/developer/applications/{application["id"]}/keys',
            json={'name': 'Private key', 'model_ids': [model_id]},
        )
    ).json()
    await grant(app, 'developer', 'developer:manage', 'developer:invoke')
    async with member(app) as client:
        assert (await client.get('/api/developer/applications')).json()['items'] == []
        path = f'/api/developer/applications/{application["id"]}'
        assert (await client.get(path)).status_code == 404
        assert (await client.post(path + '/keys', json={'name': 'steal', 'model_ids': [model_id]})).status_code == 404
        assert (await client.post(path + f'/keys/{key["id"]}/rotate', json={})).status_code == 404
        assert (
            await client.delete(path + f'/keys/{key["id"]}', headers={'content-type': 'application/json'})
        ).status_code == 404
        assert (await client.put(path + '/resources/knowledge_base/' + str(uuid4()), json={})).status_code in {404, 422}
        assert (await client.get('/api/admin/applications')).status_code == 403
        assert (await client.get('/api/admin/roles')).status_code == 403
        # Issuing a new key for another model does not bypass application ownership.
        assert (await client.patch(path, json={'name': 'steal', 'expected_version': 1})).status_code == 404


async def test_api_key_scope_models_revocation_and_console_auth_stay_independent(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    role = await grant(app, 'developer', 'developer:manage', 'developer:invoke')
    await admin.post(
        f'/api/models/{model_id}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.01'},
    )
    async with member(app) as client:
        application = (await client.post('/api/developer/applications', json={'name': 'Member app'})).json()
        path = f'/api/developer/applications/{application["id"]}'
        assert (await client.get(path + '/resource-access')).status_code == 200
        assert (await client.get(path + '/resource-candidates', params={'kind': 'knowledge_base'})).json() == []
        issued = await client.post(
            path + '/keys', json={'name': 'SDK', 'model_ids': [model_id], 'scopes': ['models:invoke']}
        )
        assert issued.status_code == 201, issued.text
        key = issued.json()
        headers = {'Authorization': 'Bearer ' + key['api_key']}
        assert [item['id'] for item in (await client.get('/v1/models', headers=headers)).json()['data']] == [model_id]
        assert (await client.get('/v1/models')).status_code == 401
        assert (await client.get('/api/v1/knowledge-bases', headers=headers)).status_code == 403
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
            assert (await anonymous.get('/api/developer/applications', headers=headers)).status_code == 401
        async with app.state.container.database.sessions.begin() as session:
            row = await session.get(IdentityRole, role)
            row.permissions = []
        assert (await client.get(path)).status_code == 403
        assert (
            await client.get('/v1/models', headers=headers)
        ).status_code == 403  # A saved key cannot exceed the owner's current module grants.
        # Owner's key is revoked through the domain to test a saved execution identity, without granting a web module.
        async with app.state.container.database.sessions.begin() as session:
            row = await session.get(IdentityRole, role)
            row.permissions = upgrade_legacy_permissions(['platform:developer:key_revoke'])
            principal = await app.state.container.identity.principal_for_user(
                session, UUID((await client.get('/api/auth/me')).json()['id'])
            )
            await app.state.container.keys.revoke(session, principal, UUID(application['id']), UUID(key['id']))
        assert (await client.get('/v1/models', headers=headers)).status_code == 401


async def test_enabled_module_is_complete_but_keeps_personal_resource_boundaries(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    await grant(app, 'agents', 'agents:manage', 'agents:publish', 'knowledge', 'skills', 'models')
    admin_agent = (
        await admin.post(
            '/api/agents', json={'name': 'Admin private assistant', 'model_id': model_id, 'max_tokens': 32}
        )
    ).json()
    async with member(app) as client:
        own = await client.post('/api/agents', json={'name': 'Own assistant', 'model_id': model_id, 'max_tokens': 32})
        assert own.status_code == 201, own.text
        assert (await client.get('/api/agents/' + admin_agent['id'])).status_code == 404
        assert (
            await client.delete('/api/agents/' + admin_agent['id'], headers={'content-type': 'application/json'})
        ).status_code == 404
        assert (
            await client.post('/api/agents/' + own.json()['id'] + '/publish', json={'expected_version': 1})
        ).status_code == 200
        assert (await client.get('/api/models/connections')).status_code == 403
        assert (
            await client.post(
                '/api/models/connections',
                json={'name': 'forged', 'base_url': 'https://example.invalid/v1', 'api_key': 'not-a-real-key'},
            )
        ).status_code == 403
        assert (await client.patch('/api/models/' + model_id, json={'enabled': False})).status_code == 403


async def test_api_cannot_issue_scope_outside_enabled_modules_and_queued_key_is_rechecked(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    from zhiheng.modules.models.public import ModelMessage

    role_id = await grant(app, 'developer', 'developer:manage', 'developer:invoke')
    await admin.post(
        f'/api/models/{model_id}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.01'},
    )
    async with member(app) as client:
        application = (await client.post('/api/developer/applications', json={'name': 'API restrictions'})).json()
        path = f'/api/developer/applications/{application["id"]}/keys'
        for scope in ('knowledge:read', 'skills:read', 'agents:invoke'):
            denied = await client.post(path, json={'name': 'forged', 'model_ids': [model_id], 'scopes': [scope]})
            assert denied.status_code == 403, (scope, denied.text)
        key = (
            await client.post(path, json={'name': 'Models only', 'model_ids': [model_id], 'scopes': ['models:invoke']})
        ).json()
        async with app.state.container.database.sessions.begin() as session:
            run, identity = await app.state.container.public_runs.admit(
                session,
                key['api_key'],
                model_id=UUID(model_id),
                operation='chat',
                messages=(ModelMessage('user', 'queued API call'),),
                options={},
                token_limit=app.state.container.settings.token_limit,
            )
        async with app.state.container.database.sessions.begin() as session:
            role = await session.get(IdentityRole, role_id)
            role.is_active = False
        await execute_run(app.state.container, run.id)
        assert not provider.requests
        async with app.state.container.database.sessions() as session:
            snapshot = await app.state.container.runs.snapshot(session, run.id, identity.owner_id)
            assert snapshot.status == 'failed' and snapshot.error.code == 'forbidden'


async def test_api_resource_scope_and_model_allowlist_cannot_reach_foreign_ids(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    from tests.core.auth_resource_support import RESOURCE_SCOPES, domain_facts, resource_world

    world = await resource_world(admin, app, provider)
    summary = await admin.get(f'/api/developer/applications/{world.application["id"]}/resource-access')
    assert summary.status_code == 200, summary.text
    await grant(
        app,
        'developer',
        'developer:manage',
        'developer:invoke',
        'knowledge',
        'knowledge:manage',
        'skills',
        'skills:manage',
    )
    async with member(app) as client:
        application = (await client.post('/api/developer/applications', json={'name': 'Unrelated application'})).json()
        key = (
            await client.post(
                f'/api/developer/applications/{application["id"]}/keys',
                json={'name': 'Own key', 'scopes': RESOURCE_SCOPES, 'model_ids': [model_id]},
            )
        ).json()
        headers = {'Authorization': 'Bearer ' + key['api_key']}
        before = await domain_facts(app)
        for path in (
            '/api/v1/files/' + world.file['id'],
            '/api/v1/knowledge-bases/' + world.base['id'],
            f'/api/v1/knowledge-bases/{world.base["id"]}/documents/{world.document["id"]}/preview',
            '/api/v1/skills/' + world.skill['id'],
            '/api/v1/operations/' + world.operation['id'],
        ):
            response = await client.get(path, headers=headers)
            assert response.status_code == 404, (path, response.text)
        forged = await client.post(
            f'/api/developer/applications/{application["id"]}/keys/{world.key["id"]}/rotate', json={}
        )
        assert forged.status_code == 404
        assert await domain_facts(app) == before
    # Even the same account's different application must have its own resource grants.
    second = (await admin.post('/api/developer/applications', json={'name': 'Same owner, different app'})).json()
    issued = await admin.post(
        f'/api/developer/applications/{second["id"]}/keys',
        json={'name': 'Other application', 'scopes': RESOURCE_SCOPES, 'model_ids': [model_id]},
    )
    assert issued.status_code == 201, issued.text
    for path in (
        '/api/v1/files/' + world.file['id'],
        '/api/v1/knowledge-bases/' + world.base['id'],
        '/api/v1/skills/' + world.skill['id'],
        '/api/v1/operations/' + world.operation['id'],
    ):
        denied = await admin.get(path, headers={'Authorization': 'Bearer ' + issued.json()['api_key']})
        assert denied.status_code == 404, (path, denied.text)
