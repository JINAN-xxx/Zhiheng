"""Module off is a real deny, and independent actions never inherit broad maintenance."""

from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_platform_module_permissions import member
from tests.core.provider import ProviderServer
from zhiheng.modules.identity.admin_orm import IdentityRole, IdentityUserRole
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.shared.permission_policy import PERMISSION_CODES, upgrade_legacy_permissions


async def allow(app: FastAPI, module: str, *actions: str, enabled: bool = True) -> UUID:
    module = module if ':' in module else 'platform:' + module
    codes = {module + ':' + action for action in actions}
    if actions:
        codes.add(module + ':read')
    if enabled:
        codes.add(module + ':enabled')
    async with app.state.container.database.sessions.begin() as session:
        user = await session.scalar(select(IdentityUser).where(IdentityUser.username == 'member'))
        role = IdentityRole(
            code='tree_' + uuid4().hex, name='Configured operation role', permissions=sorted(codes), is_active=True
        )
        session.add(role)
        await session.flush()
        session.add(IdentityUserRole(user_id=user.id, role_id=role.id))
        return role.id


async def test_catalog_is_module_tree_and_empty_module_does_not_grant_read(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    items = (await admin.get('/api/admin/permissions')).json()['items']
    assert {item['code'] for item in items} == PERMISSION_CODES
    assert all(item['kind'] in {'module', 'action'} for item in items)
    assert all(item.get('parent') == item['module'] + ':enabled' for item in items if item['kind'] == 'action')
    assert any(item['module'] == 'system:users' and item['action'] == 'assign_roles' for item in items)
    await allow(app, 'knowledge')
    async with member(app) as client:
        assert (await client.get('/api/knowledge/bases')).status_code == 403
        assert (await client.get('/api/auth/me')).json()['permissions'] == ['platform:knowledge:enabled']


async def test_create_allowed_delete_update_import_query_share_denied(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    embed = await embedding_model(admin, provider)
    await allow(app, 'knowledge', 'create')
    async with member(app) as client:
        response = await client.post('/api/knowledge/bases', json={'name': 'Create only', 'embed_model_id': embed})
        assert response.status_code == 201, response.text
        root = '/api/knowledge/bases/' + response.json()['id']
        assert (await client.get(root)).status_code == 200
        assert (await client.patch(root, json={'name': 'forged', 'expected_version': 1})).status_code == 403
        assert (
            await client.request('DELETE', root, json={'expected_version': 1, 'client_request_id': str(uuid4())})
        ).status_code == 403
        assert (
            await client.post(root + '/query', json={'query': 'forged', 'client_request_id': str(uuid4())})
        ).status_code == 403
        assert (await client.get(root + '/invitations')).status_code == 403
        assert (
            await client.post(root + '/documents', json={'file_ids': [], 'client_request_id': str(uuid4())})
        ).status_code == 403


async def test_disable_preserves_children_but_denies_console_and_effective_rights(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    role_id = await allow(app, 'knowledge', 'create')
    async with member(app) as client:
        original = (await admin.get('/api/admin/roles/' + str(role_id))).json()
        children = [code for code in original['permissions'] if not code.endswith(':enabled')]
        disabled = await admin.patch(
            '/api/admin/roles/' + str(role_id), json={'permissions': children, 'expected_version': original['version']}
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()['permissions'] == sorted(children)
        assert (await client.get('/api/knowledge/bases')).status_code == 403
        assert (await client.get('/api/auth/me')).json()['permissions'] == []
        restored = await admin.patch(
            '/api/admin/roles/' + str(role_id),
            json={'permissions': original['permissions'], 'expected_version': disabled.json()['version']},
        )
        assert restored.status_code == 200
        assert (await client.get('/api/knowledge/bases')).status_code == 200


async def test_read_dependency_and_large_complete_role_are_validated(admin: httpx.AsyncClient) -> None:
    invalid = await admin.post(
        '/api/admin/roles',
        json={'name': 'Missing base read', 'permissions': ['platform:knowledge:enabled', 'platform:knowledge:create']},
    )
    assert invalid.status_code == 400 and invalid.json()['error']['code'] == 'permission_dependency'
    complete = await admin.post(
        '/api/admin/roles', json={'name': 'All explicit actions', 'permissions': sorted(PERMISSION_CODES)}
    )
    assert complete.status_code == 201, complete.text
    assert set(complete.json()['permissions']) == PERMISSION_CODES


async def test_model_and_credential_select_all_are_independent(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    await allow(app, 'models', 'create')
    async with member(app) as client:
        choices = await client.get('/api/models/connection-options')
        assert choices.status_code == 200 and all(set(row) == {'id', 'name', 'enabled'} for row in choices.json())
        configured = await client.post(
            '/api/models',
            json={
                'name': 'Created model',
                'connection_id': choices.json()[0]['id'],
                'upstream_model': 'local-text',
                'max_output_tokens': 32,
            },
        )
        assert configured.status_code == 201, configured.text
        assert (
            await client.delete('/api/models/' + configured.json()['id'], headers={'content-type': 'application/json'})
        ).status_code == 403
        assert (await client.get('/api/models/connections')).status_code == 403
        assert (await client.patch('/api/models/' + model_id, json={'enabled': False})).status_code == 403
        assert (
            await client.post('/api/models/' + model_id + '/test', json={'client_request_id': str(uuid4())})
        ).status_code == 403


async def test_user_create_does_not_delete_reset_or_assign_roles(admin: httpx.AsyncClient, app: FastAPI) -> None:
    await allow(app, 'system:users', 'create')
    role = (await admin.post('/api/admin/roles', json={'name': 'Empty assignment'})).json()
    async with member(app) as client:
        created = await client.post(
            '/api/admin/users', json={'username': 'created_' + uuid4().hex[:8], 'password': 'platform-test-password'}
        )
        assert created.status_code == 201, created.text
        target = '/api/admin/users/' + created.json()['id']
        assert (
            await client.request(
                'DELETE', target, params={'expected_version': 1}, headers={'content-type': 'application/json'}
            )
        ).status_code == 403
        assert (
            await client.post(target + '/password', json={'password': 'another-test-password', 'expected_version': 1})
        ).status_code == 403
        assert (
            await client.post(
                '/api/admin/users',
                json={
                    'username': 'withrole_' + uuid4().hex[:8],
                    'password': 'platform-test-password',
                    'role_ids': [role['id']],
                },
            )
        ).status_code == 403


async def test_api_knowledge_write_scope_cannot_bypass_create_only_role(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    embed = await embedding_model(admin, provider)
    role_id = await allow(app, 'knowledge', 'create')
    await allow(app, 'developer', 'create', 'key_create', 'invoke', 'grant')
    async with member(app) as client:
        application = (await client.post('/api/developer/applications', json={'name': 'Scoped SDK'})).json()
        issued = await client.post(
            '/api/developer/applications/' + application['id'] + '/keys',
            json={'name': 'Create only', 'scopes': ['knowledge:write', 'knowledge:read'], 'model_ids': [embed]},
        )
        assert issued.status_code == 201, issued.text
        headers = {'Authorization': 'Bearer ' + issued.json()['api_key']}
        created = await client.post(
            '/api/v1/knowledge-bases', headers=headers, json={'name': 'API create only', 'embed_model_id': embed}
        )
        assert created.status_code == 201, created.text
        target = '/api/v1/knowledge-bases/' + created.json()['id']
        denied = await client.delete(target, headers={**headers, 'If-Match': created.headers['etag']})
        assert denied.status_code == 403, denied.text
        async with app.state.container.database.sessions.begin() as session:
            role = await session.get(IdentityRole, role_id)
            role.permissions = [code for code in role.permissions if code != 'platform:knowledge:enabled']
        assert (await client.get(target, headers=headers)).status_code == 403


def test_legacy_conversion_preserves_scope_without_credential_escalation() -> None:
    used = upgrade_legacy_permissions(['platform:knowledge:access', 'platform:models:access'])
    assert 'platform:knowledge:query' in used and 'platform:knowledge:delete' not in used
    assert not any(code.startswith('ai:credentials:') for code in used)
    managed = upgrade_legacy_permissions(['platform:knowledge:manage'])
    assert {'platform:knowledge:create', 'platform:knowledge:delete', 'platform:knowledge:import'} <= set(managed)
    assert 'platform:knowledge:query' not in managed and 'platform:knowledge:share' not in managed
    system = upgrade_legacy_permissions(['system:users:write'])
    assert {'system:users:create', 'system:users:delete', 'system:users:assign_roles'} <= set(system)


async def test_user_edit_and_role_edit_cannot_escalate_assignment(admin: httpx.AsyncClient, app: FastAPI) -> None:
    await allow(app, 'system:users', 'update')
    await allow(app, 'system:roles', 'update')
    await allow(app, 'models', 'read')
    roles = (await admin.post('/api/admin/roles', json={'name': 'Unprivileged target'})).json()
    users = (await admin.get('/api/admin/users', params={'search': 'member'})).json()['items']
    target = next(user for user in users if user['username'] == 'member')
    async with member(app) as client:
        denied = await client.patch(
            '/api/admin/users/' + target['id'], json={'role_ids': [roles['id']], 'expected_version': target['version']}
        )
        assert denied.status_code == 403, denied.text
        denied = await client.patch(
            '/api/admin/roles/' + roles['id'],
            json={
                'permissions': ['platform:models:enabled', 'platform:models:read'],
                'expected_version': roles['version'],
            },
        )
        assert denied.status_code == 403, denied.text


async def test_parameter_create_does_not_authorize_global_settings_update(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    await allow(app, 'system:configs', 'create')
    async with member(app) as client:
        assert (await client.get('/api/knowledge/settings')).status_code == 200
        assert (await client.patch('/api/knowledge/settings', json={})).status_code == 403


async def test_import_permission_completes_owned_import_without_query_or_delete(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from tests.core.integration.test_knowledge import VectorFixture
    from tests.core.integration.test_knowledge_failures import finish_job

    embed = await embedding_model(admin, provider)
    role_id = await allow(app, 'knowledge', 'create')
    app.state.container.knowledge_jobs.vectors = VectorFixture()
    async with member(app) as client:
        base = (
            await client.post('/api/knowledge/bases', json={'name': 'Import-only workflow', 'embed_model_id': embed})
        ).json()
        role = (await admin.get('/api/admin/roles/' + str(role_id))).json()
        changed = await admin.patch(
            '/api/admin/roles/' + str(role_id),
            json={
                'permissions': ['platform:knowledge:enabled', 'platform:knowledge:read', 'platform:knowledge:import'],
                'expected_version': role['version'],
            },
        )
        assert changed.status_code == 200, changed.text
        uploaded = await client.post(
            '/api/files?filename=workflow.txt',
            content=b'Owned import content',
            headers={'content-type': 'application/octet-stream'},
        )
        assert uploaded.status_code == 201, uploaded.text
        queued = await client.post(
            '/api/knowledge/bases/' + base['id'] + '/documents',
            json={'file_ids': [uploaded.json()['id']], 'client_request_id': str(uuid4())},
        )
        assert queued.status_code == 202, queued.text
        completed = await finish_job(client, app, queued.json())
        assert completed['status'] == 'completed', completed
        assert (
            await client.post(
                '/api/knowledge/bases/' + base['id'] + '/query',
                json={'query': 'not allowed', 'client_request_id': str(uuid4())},
            )
        ).status_code == 403


def test_disabled_module_children_never_become_effective_without_gate() -> None:
    from zhiheng.shared.permission_policy import effective_permissions, is_granted

    children = {'platform:knowledge:read', 'platform:knowledge:create', 'platform:knowledge:delete'}
    assert effective_permissions(children) == frozenset()
    assert not is_granted(children, 'platform:knowledge:manage')
    enabled = children | {'platform:knowledge:enabled'}
    assert is_granted(enabled, 'platform:knowledge:create') and is_granted(enabled, 'platform:knowledge:delete')
    assert not is_granted(enabled, 'platform:knowledge:query')


async def test_another_role_cannot_reactivate_disabled_module_children(admin: httpx.AsyncClient, app: FastAPI) -> None:
    await allow(app, 'knowledge', 'create', 'delete', enabled=False)
    await allow(app, 'knowledge', 'read')
    async with member(app) as client:
        principal = (await client.get('/api/auth/me')).json()
        assert set(principal['permissions']) == {'platform:knowledge:enabled', 'platform:knowledge:read'}
        assert (await client.get('/api/knowledge/bases')).status_code == 200
        denied = await client.post('/api/knowledge/bases', json={'name': 'Retained grant must stay inactive'})
        assert denied.status_code == 403, denied.text
        users = (await admin.get('/api/admin/users', params={'search': 'member'})).json()['items']
        target = next(user for user in users if user['username'] == 'member')
        info = (await admin.get('/api/admin/users/' + target['id'] + '/effective-permissions')).json()
        assert {item['code'] for item in info['items']} == set(principal['permissions'])


async def test_admin_personal_api_keeps_functional_rights_without_foreign_resource_access(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from tests.core.integration.test_resource_files import auth
    from tests.core.integration.test_resource_key_scopes import application, issue

    model = await embedding_model(admin, provider)
    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['knowledge:read', 'knowledge:write'], model_ids=[model])
    created = await admin.post(
        '/api/v1/knowledge-bases', headers=auth(key), json={'name': 'Admin owned', 'embed_model_id': model}
    )
    assert created.status_code == 201, created.text
    assert (await admin.get('/api/v1/knowledge-bases/' + created.json()['id'], headers=auth(key))).status_code == 200
    await allow(app, 'knowledge', 'create')
    async with member(app) as client:
        foreign = await client.post(
            '/api/knowledge/bases', json={'name': 'Private member library', 'embed_model_id': model}
        )
        assert foreign.status_code == 201, foreign.text
    version = (await admin.get('/api/developer/applications/' + owner_app['id'])).json()['version']
    bound = await admin.put(
        '/api/developer/applications/' + owner_app['id'] + '/resources/knowledge_base/' + foreign.json()['id'],
        json={
            'actions': ['read'],
            'expected_version': version,
        },
    )
    assert bound.status_code == 404, bound.text
    assert (await admin.get('/api/v1/knowledge-bases/' + foreign.json()['id'], headers=auth(key))).status_code == 404
