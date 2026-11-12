"""Authorization matrices use real users, current roles, and existing target records.

Every parameter ID names the operation under review. Provider fixtures are local;
these tests never execute a model request or use deployment credentials.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_ai_management_permissions import account
from tests.core.integration.test_billing_http import mutation, point_rate
from tests.core.integration.test_resource_key_scopes import application, issue

PASSWORD = 'platform-test-password'
NEW_PASSWORD = 'authorization-new-password'
JSON_HEADERS = {'content-type': 'application/json'}


def check(response: httpx.Response, status: int, case_id: str) -> httpx.Response:
    assert response.status_code == status, f'{case_id}: {response.status_code} {response.text}'
    return response


@asynccontextmanager
async def member(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test', headers=JSON_HEADERS
    ) as client:
        check(await client.post('/api/auth/login', json={'username': 'member', 'password': PASSWORD}), 200, 'login')
        yield client


def create_fields(area: str) -> dict[str, Any]:
    code = 'audit_' + uuid4().hex
    return {
        'users': {'username': code, 'password': PASSWORD, 'display_name': code},
        'roles': {'code': code, 'name': code, 'permissions': []},
        'departments': {'name': code},
        'configs': {'key': 'console.notice_enabled', 'name': code, 'value': 'true'},
        'notices': {'title': code, 'content': code},
    }[area]


def patch_fields(area: str) -> dict[str, Any]:
    field = {'users': 'display_name', 'notices': 'title'}.get(area, 'name')
    return {field: 'changed_' + uuid4().hex, 'expected_version': 1}


AREAS = ('users', 'roles', 'departments', 'configs', 'notices')
CRUD = ('list', 'detail', 'create', 'patch', 'delete')


@pytest.mark.parametrize(
    'area,operation', [(a, o) for a in AREAS for o in CRUD], ids=[f'system-{a}-{o}' for a in AREAS for o in CRUD]
)
async def test_system_crud_minimum_roles_existing_targets(
    admin: httpx.AsyncClient, app: FastAPI, area: str, operation: str
) -> None:
    permission_area = area
    collection = '/api/admin/' + area
    target = None
    if operation != 'create':
        target = check(await admin.post(collection, json=create_fields(area)), 201, 'existing-target').json()
    path = collection if operation in ('list', 'create') else collection + '/' + target['id']
    verb = {'list': 'GET', 'detail': 'GET', 'create': 'POST', 'patch': 'PATCH', 'delete': 'DELETE'}[operation]
    body = create_fields(area) if operation == 'create' else patch_fields(area) if operation == 'patch' else None
    kwargs: dict[str, Any] = {'json': body} if body is not None else {}
    if operation == 'delete':
        kwargs['params'] = {'expected_version': 1}
    case_id = f'system-{area}-{operation}'
    async with (
        member(app) as basic,
        account(admin, app, f'system:{permission_area}:read') as (reader, _, _),
        account(admin, app, f'system:{permission_area}:write') as (writer, _, _),
    ):
        before = (await admin.get(collection)).json()
        check(await basic.request(verb, path, **kwargs), 403, case_id + '-basic-denied')
        denied = writer if operation in ('list', 'detail') else reader
        allowed = reader if operation in ('list', 'detail') else writer
        check(await denied.request(verb, path, **kwargs), 403, case_id + '-opposite-role-denied')
        # A denial must preserve a real object; a 403 alone could mask partial writes.
        if target is not None:
            assert check(await admin.get(collection + '/' + target['id']), 200, case_id).json() == target
        else:
            assert (await admin.get(collection)).json() == before
        response = check(
            await allowed.request(verb, path, **kwargs),
            204 if operation == 'delete' else 201 if operation == 'create' else 200,
            case_id + '-minimum-role-allowed',
        )
        if operation == 'list':
            assert target['id'] in {row['id'] for row in response.json()['items']}
        elif operation == 'detail':
            assert response.json() == target
        elif operation == 'delete':
            check(await admin.get(path), 404, case_id + '-deleted')
        else:
            actual = check(await admin.get(collection + '/' + response.json()['id']), 200, case_id).json()
            assert actual == response.json()
            assert PASSWORD not in response.text and 'password_hash' not in response.text


@pytest.mark.parametrize(
    'path,permission',
    [
        ('/api/admin/permissions', 'system:roles:read'),
        ('/api/admin/users/options', 'system:users:read'),
        ('/api/admin/monitor', 'system:monitor:read'),
        ('/api/admin/logs', 'system:logs:read'),
        ('/api/admin/sessions', 'system:sessions:read'),
    ],
    ids=[
        'permissions-directory',
        'user-options',
        'monitor-read',
        'logs-read',
        'sessions-read',
    ],
)
async def test_system_special_reads_require_their_current_permission(
    admin: httpx.AsyncClient, app: FastAPI, path: str, permission: str
) -> None:
    async with member(app) as basic, account(admin, app, permission) as (allowed, role, _):
        check(await basic.get(path), 403, path + '-basic')
        response = check(await allowed.get(path), 200, path + '-minimum-role')
        assert PASSWORD not in response.text and 'token_hash' not in response.text
        check(
            await admin.patch(
                f'/api/admin/roles/{role["id"]}', json={'permissions': [], 'expected_version': role['version']}
            ),
            200,
            'revoke-role',
        )
        check(await allowed.get(path), 403, path + '-old-cookie-revoked')


async def test_delegated_user_and_role_writers_cannot_elevate_or_take_over(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    async with (
        account(admin, app, 'system:users:write', 'system:roles:write') as (manager, role, user),
        account(admin, app, 'system:logs:read') as (powerful, powerful_role, powerful_user),
    ):
        before = (await admin.get(f'/api/admin/users/{powerful_user["id"]}')).json()
        attacks = [
            ('POST', '/api/admin/users', {**create_fields('users'), 'is_admin': True}),
            ('PATCH', f'/api/admin/users/{user["id"]}', {'is_admin': True, 'expected_version': 1}),
            (
                'PATCH',
                f'/api/admin/users/{user["id"]}',
                {'role_ids': [role['id'], powerful_role['id']], 'expected_version': 1},
            ),
            (
                'PATCH',
                f'/api/admin/roles/{role["id"]}',
                {
                    'permissions': ['system:users:write', 'system:roles:write', 'system:logs:read'],
                    'expected_version': 1,
                },
            ),
            ('POST', '/api/admin/roles', {**create_fields('roles'), 'permissions': ['system:logs:read']}),
            ('PATCH', f'/api/admin/users/{powerful_user["id"]}', {'display_name': 'takeover', 'expected_version': 1}),
            (
                'POST',
                f'/api/admin/users/{powerful_user["id"]}/password',
                {'password': NEW_PASSWORD, 'expected_version': 1},
            ),
        ]
        for verb, path, payload in attacks:
            check(await manager.request(verb, path, json=payload), 403, 'no-escalation-' + path)
        assert (await admin.get(f'/api/admin/users/{powerful_user["id"]}')).json() == before
        assert (await powerful.get('/api/auth/me')).json()['permissions'] == ['system:logs:read']
        assert set((await manager.get('/api/auth/me')).json()['permissions']) == {
            'system:users:write',
            'system:roles:write',
        }


async def test_password_reset_and_session_revocation_obey_target_boundaries(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    async with (
        account(admin, app) as (target, _, target_user),
        account(admin, app, 'system:sessions:write') as (kicker, _, kicker_user),
        account(admin, app, 'system:users:write') as (resetter, _, _),
    ):
        sessions = (await admin.get('/api/admin/sessions')).json()['items']
        victim = next(row for row in sessions if row['user_id'] == target_user['id'])
        admin_session = next(row for row in sessions if row['is_current'])
        kicker_session = next(row for row in sessions if row['user_id'] == kicker_user['id'])
        before = (await admin.get('/api/admin/sessions')).json()
        check(
            await target.delete(f'/api/admin/sessions/{victim["session_id"]}', params={'expected_version': 1}),
            403,
            'ordinary-user-session-management',
        )
        check(
            await kicker.delete(f'/api/admin/sessions/{admin_session["session_id"]}', params={'expected_version': 1}),
            403,
            'non-admin-cannot-kick-admin',
        )
        check(
            await kicker.delete(f'/api/admin/sessions/{kicker_session["session_id"]}', params={'expected_version': 1}),
            409,
            'current-session-protected',
        )
        assert (await admin.get('/api/admin/sessions')).json() == before
        check(
            await kicker.delete(f'/api/admin/sessions/{victim["session_id"]}', params={'expected_version': 1}),
            204,
            'delegated-kick-regular-user',
        )
        check(await target.get('/api/auth/me'), 401, 'kicked-cookie')
        check(
            await target.post('/api/auth/login', json={'username': target_user['username'], 'password': PASSWORD}),
            200,
            'relogin',
        )
        check(
            await resetter.post(
                f'/api/admin/users/{target_user["id"]}/password', json={'password': NEW_PASSWORD, 'expected_version': 1}
            ),
            200,
            'delegated-reset-regular-user',
        )
        check(await target.get('/api/auth/me'), 401, 'password-reset-revokes-cookie')
        check(
            await target.post('/api/auth/login', json={'username': target_user['username'], 'password': PASSWORD}),
            401,
            'old-password-rejected',
        )
        check(
            await target.post('/api/auth/login', json={'username': target_user['username'], 'password': NEW_PASSWORD}),
            200,
            'new-password-works',
        )


@pytest.mark.parametrize(
    'operation', ['list', 'detail', 'patch', 'delete', 'enable', 'disable', 'usage'], ids=lambda x: 'developer-app-' + x
)
async def test_developer_application_owner_isolation_and_explicit_admin_management(
    admin: httpx.AsyncClient, app: FastAPI, operation: str
) -> None:
    async with member(app) as owner, account(admin, app) as (other, _, _):
        pairs = [(client, await application(client)) for client in (owner, other, admin)]
        if operation == 'enable':
            for client, row in pairs:
                check(
                    await client.post(f'/api/developer/applications/{row["id"]}/disable', json={}),
                    200,
                    'prepare-own-disabled',
                )
        for target_client, row in pairs:
            base = '/api/developer/applications/' + row['id']
            before = check(await target_client.get(base), 200, 'owner-snapshot').json()
            for actor, owned in pairs:
                if actor is target_client:
                    continue
                if operation == 'list':
                    items = check(await actor.get('/api/developer/applications'), 200, 'own-list').json()['items']
                    assert {item['id'] for item in items} == {owned['id']}
                    continue
                verb = (
                    'GET'
                    if operation in ('detail', 'usage')
                    else 'PATCH'
                    if operation == 'patch'
                    else 'DELETE'
                    if operation == 'delete'
                    else 'POST'
                )
                suffix = '/' + operation if operation in ('usage', 'enable', 'disable') else ''
                payload = (
                    {'name': 'Forbidden', 'expected_version': before['version']}
                    if operation == 'patch'
                    else {}
                    if verb == 'POST'
                    else None
                )
                check(
                    await actor.request(
                        verb,
                        base + suffix,
                        headers=JSON_HEADERS if verb == 'DELETE' else {},
                        **({'json': payload} if payload is not None else {}),
                    ),
                    404,
                    'foreign-personal-app',
                )
            assert (await target_client.get(base)).json() == before
        for client, row in pairs:
            base = '/api/developer/applications/' + row['id']
            current = (await client.get(base)).json()
            if operation == 'list':
                check(await client.get('/api/developer/applications/scope-catalog'), 200, 'scope-catalog')
                check(
                    await client.post(
                        '/api/developer/applications', json={'name': 'Injected', 'owner_id': pairs[0][1]['owner_id']}
                    ),
                    422,
                    'cannot-pick-owner',
                )
            elif operation in ('detail', 'usage'):
                check(await client.get(base + ('/usage' if operation == 'usage' else '')), 200, 'own-personal-read')
            elif operation == 'patch':
                check(
                    await client.patch(base, json={'name': 'Managed', 'expected_version': current['version']}),
                    200,
                    'own-edit',
                )
            elif operation == 'delete':
                check(await client.delete(base, headers=JSON_HEADERS), 200, 'own-delete')
            else:
                check(await client.post(base + '/' + operation, json={}), 200, 'own-transition')


@pytest.mark.parametrize(
    'operation', ['list', 'detail', 'create', 'rotate', 'revoke'], ids=lambda x: 'developer-key-' + x
)
async def test_developer_keys_require_application_owner_and_matching_parent(
    admin: httpx.AsyncClient, app: FastAPI, operation: str
) -> None:
    async with member(app) as owner, account(admin, app) as (other, _, _):
        clients = (owner, other, admin)
        apps = [await application(client) for client in clients]
        keys = [
            await issue(client, row['id'], scopes=['files:read']) for client, row in zip(clients, apps, strict=True)
        ]
        payload = (
            {'name': 'Client', 'scopes': ['files:read']}
            if operation == 'create'
            else {'grace_period_seconds': 0}
            if operation == 'rotate'
            else {}
        )
        verb = 'GET' if operation in ('list', 'detail') else 'POST'
        for index, client in enumerate(clients):
            own_app, own_key = apps[index], keys[index]
            for foreign_index, foreign in enumerate(apps):
                if foreign_index == index:
                    continue
                key = keys[foreign_index]
                base = f'/api/developer/applications/{foreign["id"]}/keys'
                path = (
                    base
                    if operation in ('list', 'create')
                    else base + '/' + key['id'] + ('/' + operation if operation in ('rotate', 'revoke') else '')
                )
                before = (await clients[foreign_index].get(base)).json()
                check(
                    await client.request(verb, path, **({'json': payload} if verb == 'POST' else {})),
                    404,
                    'foreign-key-denied',
                )
                assert (await clients[foreign_index].get(base)).json() == before
                if operation not in ('list', 'create'):
                    wrong_parent = f'/api/developer/applications/{own_app["id"]}/keys/{key["id"]}'
                    if operation in ('rotate', 'revoke'):
                        wrong_parent += '/' + operation
                    check(
                        await client.request(verb, wrong_parent, **({'json': payload} if verb == 'POST' else {})),
                        404,
                        'key-parent-mismatch',
                    )
            base = f'/api/developer/applications/{own_app["id"]}/keys'
            path = (
                base
                if operation in ('list', 'create')
                else base + '/' + own_key['id'] + ('/' + operation if operation in ('rotate', 'revoke') else '')
            )
            result = check(
                await client.request(verb, path, **({'json': payload} if verb == 'POST' else {})),
                201 if operation in ('create', 'rotate') else 200,
                'own-key-management',
            )
            if operation not in ('create', 'rotate'):
                assert 'api_key' not in result.text and 'digest' not in result.text
            listed = check(await client.get(base), 200, 'own-key-list')
            assert all(secret['api_key'] not in listed.text for secret in keys)


@pytest.mark.parametrize(
    'operation', ['summary', 'candidates', 'grants', 'bind', 'unbind', 'quota'], ids=lambda x: 'developer-resource-' + x
)
async def test_developer_resource_ownership_and_quota_admin_exception(
    admin: httpx.AsyncClient, app: FastAPI, operation: str
) -> None:
    async with member(app) as owner, account(admin, app) as (other, _, _):
        clients = (owner, other)
        apps = [await application(client) for client in clients]
        files = []
        for client in clients:
            result = check(
                await client.post(
                    '/api/files?filename=private-audit.txt',
                    content=uuid4().hex.encode(),
                    headers={'content-type': 'application/octet-stream'},
                ),
                201,
                'private-source',
            )
            files.append(result.json())
        for index, client in enumerate(clients):
            base = '/api/developer/applications/' + apps[index]['id']
            bound = check(
                await client.put(
                    base + '/resources/file/' + files[index]['id'], json={'actions': ['read'], 'expected_version': 1}
                ),
                200,
                'owner-initial-bind',
            )
            apps[index]['version'] = bound.json()['application_version']
        for index, client in enumerate(clients):
            foreign, file = apps[1 - index], files[1 - index]
            base = '/api/developer/applications/' + foreign['id']
            suffix = {
                'summary': '/resource-access',
                'candidates': '/resource-candidates?kind=file',
                'grants': '/resources',
                'bind': '/resources/file/' + file['id'],
                'unbind': '/resources/file/' + file['id'],
                'quota': '/resource-quota',
            }[operation]
            verb = {'bind': 'PUT', 'unbind': 'DELETE', 'quota': 'PATCH'}.get(operation, 'GET')
            payload = {'expected_version': foreign['version']}
            if operation == 'bind':
                payload['actions'] = ['read']
            if operation == 'quota':
                payload.update(source_file_limit=7, source_byte_limit=4096)
            before = (await clients[1 - index].get(base)).json()
            check(
                await client.request(verb, base + suffix, **({'json': payload} if verb != 'GET' else {})),
                403 if operation == 'quota' else 404,
                'foreign-resource-' + operation,
            )
            assert (await clients[1 - index].get(base)).json() == before
            assert len((await clients[1 - index].get(base + '/resources')).json()) == 1
        base = '/api/developer/applications/' + apps[0]['id']
        version = apps[0]['version']
        if operation == 'summary':
            check(await admin.get(base + '/resource-access'), 404, 'admin-has-no-personal-summary')
            assert (await owner.get(base + '/resource-access')).json()['items'][0]['resource_id'] == files[0]['id']
        elif operation == 'quota':
            payload = {'expected_version': version, 'source_file_limit': 7, 'source_byte_limit': 4096}
            check(await owner.patch(base + '/resource-quota', json=payload), 403, 'owner-cannot-change-quota')
            check(
                await admin.patch(base + '/resource-quota', json=payload),
                404,
                'personal-quota-route-does-not-bypass-owner',
            )
            check(
                await admin.patch('/api/admin/applications/' + apps[0]['id'] + '/resource-quota', json=payload),
                200,
                'separate-admin-quota',
            )
        elif operation == 'bind':
            payload = {'actions': ['read'], 'expected_version': version}
            check(
                await admin.put(base + '/resources/file/' + files[0]['id'], json=payload),
                404,
                'admin-cannot-bind-foreign-app',
            )
            check(
                await owner.put(base + '/resources/file/' + files[1]['id'], json=payload),
                404,
                'owner-cannot-bind-foreign-private-file',
            )
            check(await owner.put(base + '/resources/file/' + files[0]['id'], json=payload), 200, 'owner-bind-allowed')
        elif operation == 'unbind':
            path = base + '/resources/file/' + files[0]['id']
            check(
                await admin.request('DELETE', path, json={'expected_version': version}),
                404,
                'admin-cannot-unbind-foreign-app',
            )
            check(await owner.request('DELETE', path, json={'expected_version': version}), 200, 'owner-unbind-allowed')
        else:
            suffix = '/resource-candidates?kind=file' if operation == 'candidates' else '/resources'
            check(await admin.get(base + suffix), 404, 'admin-has-no-personal-resource-bypass')
            data = check(await owner.get(base + suffix), 200, 'owner-resource-read').json()
            assert files[0]['id'] in {row['resource_id'] for row in data}
            assert files[1]['id'] not in {row['resource_id'] for row in data}


@pytest.mark.parametrize('operation', ['profile', 'password', 'logout'], ids=lambda x: 'auth-self-' + x)
async def test_authentication_self_routes_reject_other_user_fields_and_preserve_other_session(
    admin: httpx.AsyncClient, app: FastAPI, operation: str
) -> None:
    async with member(app) as owner, account(admin, app) as (other, _, other_user):
        before = (await other.get('/api/auth/profile')).json()
        mine = (await owner.get('/api/auth/profile')).json()
        assert (
            check(
                await owner.get('/api/auth/profile', params={'user_id': other_user['id']}),
                200,
                'profile-query-cannot-select-other',
            ).json()['id']
            == mine['id']
        )
        if operation == 'profile':
            for fields in (
                {'id': other_user['id']},
                {'user_id': other_user['id']},
                {'is_admin': True},
                {'role_ids': []},
                {'is_active': False},
            ):
                check(
                    await owner.patch(
                        '/api/auth/profile',
                        json={'display_name': 'attack', 'expected_version': mine['version'], **fields},
                    ),
                    422,
                    'profile-field-not-writable',
                )
            check(
                await owner.patch(
                    '/api/auth/profile', json={'display_name': 'own-change', 'expected_version': mine['version']}
                ),
                200,
                'own-profile-change',
            )
        elif operation == 'password':
            body = {'current_password': PASSWORD, 'new_password': NEW_PASSWORD, 'expected_version': mine['version']}
            check(
                await owner.post('/api/auth/password', json={**body, 'user_id': other_user['id']}),
                422,
                'password-cannot-select-other',
            )
            check(await owner.post('/api/auth/password', json=body), 204, 'own-password-change')
            check(await owner.get('/api/auth/me'), 401, 'password-clears-own-session')
        else:
            check(
                await owner.post('/api/auth/logout', params={'user_id': other_user['id']}, json={}), 204, 'own-logout'
            )
            check(await owner.get('/api/auth/me'), 401, 'logout-clears-own-session')
        assert (await other.get('/api/auth/profile')).json() == before
        check(await other.get('/api/auth/me'), 200, 'other-session-survives')


async def test_billing_real_foreign_accounts_read_write_and_nonempty_self_projection(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    async with (
        member(app) as owner,
        account(admin, app, 'ai:credits:read') as (reader, _, _),
        account(admin, app, 'ai:credits:write') as (writer, _, _),
        account(admin, app) as (other, _, other_user),
    ):
        identifier = other_user['id']
        path = f'/api/billing/accounts/{identifier}'
        check(
            await writer.post(path + '/adjust', json=mutation(kind='grant', points='23')),
            200,
            'writer-can-grant-foreign-account',
        )
        before = (await admin.get(path + '/ledger')).json()
        assert before['total'] == 1
        for suffix in ('/ledger', '/usage'):
            check(await owner.get(path + suffix), 403, 'basic-cannot-read-real-foreign-' + suffix)
            check(await reader.get(path + suffix), 200, 'reader-can-read-foreign-' + suffix)
            check(await writer.get(path + suffix), 200, 'writer-can-read-foreign-' + suffix)
        for client in (owner, reader):
            check(await client.post(path + '/adjust', json=mutation(kind='grant', points='3')), 403, 'no-credit-write')
            check(
                await client.patch(path + '/state', json=mutation(enabled=False, expected_version=1)),
                403,
                'no-credit-state-write',
            )
        assert (await admin.get(path + '/ledger')).json() == before
        assert (await owner.get('/api/billing/me/ledger', params={'user_id': identifier})).json()['items'] == []
        assert (await other.get('/api/billing/me/ledger')).json() == before
        assert (await owner.get('/api/billing/me', params={'user_id': identifier})).json()['user_id'] != identifier
        assert Decimal((await other.get('/api/billing/me')).json()['available_points']) == 23
        check(
            await writer.patch(path + '/state', json=mutation(enabled=False, expected_version=1)),
            200,
            'writer-can-pause-foreign-account',
        )


async def test_billing_unknown_usage_reconciliation_requires_exact_write_permission(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    await point_rate(admin, model_id)
    async with (
        account(admin, app) as (owner, _, owner_user),
        account(admin, app, 'ai:credits:read') as (reader, _, _),
        account(admin, app, 'ai:credits:write') as (writer, _, _),
    ):
        identifier = UUID(owner_user['id'])
        check(
            await admin.post(f'/api/billing/accounts/{identifier}/adjust', json=mutation(kind='grant', points='100')),
            200,
            'grant-target',
        )
        check(
            await admin.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0)),
            200,
            'enable-billing',
        )
        run_id = uuid4()
        async with app.state.container.database.sessions.begin() as session:
            await app.state.container.usage.reserve(
                session,
                user_id=identifier,
                run_id=run_id,
                model_id=UUID(model_id),
                source='test',
                tokens=2000,
                token_limit=10000,
                estimated_input_tokens=1000,
                max_output_tokens=1000,
            )
            await app.state.container.usage.settle(session, run_id, input_tokens=1000)
        before = (await owner.get('/api/billing/me/usage')).json()
        body = mutation(
            expected_version=before['items'][0]['version'],
            input_tokens=1000,
            output_tokens=200,
            cache_tokens=0,
            search_units=0,
        )
        path = f'/api/billing/usage/{run_id}/reconcile'
        for client in (owner, reader):
            check(await client.post(path, json=body), 403, 'real-run-no-reconcile-write')
        assert (await owner.get('/api/billing/me/usage')).json() == before
        check(await writer.post(path, json=body), 200, 'writer-can-reconcile-foreign-run')
        assert (await owner.get('/api/billing/me/usage')).json()['items'][0]['status'] == 'settled'
        # Explicit credit-read authority may select another account; self views remain owner-scoped.
        assert (await reader.get('/api/usage', params={'user_id': str(identifier)})).json()['records'][0][
            'run_id'
        ] == str(run_id)
        assert (await owner.get('/api/usage')).json()['records'][0]['run_id'] == str(run_id)
        assert (await admin.get('/api/usage', params={'user_id': str(identifier)})).json()['records'][0][
            'run_id'
        ] == str(run_id)


async def test_model_credentials_rates_and_admin_only_switches_stay_separate(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    async with (
        account(admin, app, 'ai:models:read') as (reader, _, _),
        account(admin, app, 'ai:rates:read') as (rates_reader, _, _),
        account(admin, app, 'ai:rates:write') as (rates_writer, _, _),
    ):
        connection = check(await reader.get('/api/models/connections'), 200, 'model-reader-connections')
        assert 'local-test-key' not in connection.text and 'encrypted_api_key' not in connection.text
        assert all(
            set(row) == {'id', 'name', 'provider', 'base_url', 'has_api_key', 'enabled'} for row in connection.json()
        )
        check(
            await rates_reader.post(
                f'/api/billing/models/{model_id}/rates', json=mutation(billing_mode='token', input_points='1')
            ),
            403,
            'rates-reader-no-point-write',
        )
        rate = check(
            await rates_writer.post(
                f'/api/billing/models/{model_id}/rates', json=mutation(billing_mode='token', input_points='1')
            ),
            201,
            'rates-writer-point-rate',
        )
        check(await admin.post(f'/api/models/{model_id}/disable', json={}), 200, 'hide-model')
        for client in (reader, rates_reader, rates_writer):
            assert (
                check(
                    await client.get(f'/api/billing/models/{model_id}/quote'), 200, 'configuration-reader-private-quote'
                ).json()
                == rate.json()
            )
        async with member(app) as basic:
            check(await basic.get(f'/api/billing/models/{model_id}/quote'), 404, 'basic-cannot-quote-disabled-model')
        catalog = (await admin.get('/api/admin/permissions')).json()['items']
        async with account(admin, app, *(row['code'] for row in catalog)) as (all_permissions, _, _):
            assert not (await all_permissions.get('/api/auth/me')).json()['is_admin']
            check(
                await all_permissions.patch('/api/billing/state', json=mutation(enabled=False, expected_version=0)),
                403,
                'all-permissions-still-not-admin-switch',
            )
            owner_app = await application(all_permissions)
            check(
                await all_permissions.patch(
                    f'/api/developer/applications/{owner_app["id"]}/resource-quota',
                    json={'source_file_limit': 7, 'source_byte_limit': 4096, 'expected_version': 1},
                ),
                403,
                'all-permissions-still-not-admin-quota',
            )


async def test_health_public_and_bearer_does_not_authenticate_console(admin: httpx.AsyncClient, app: FastAPI) -> None:
    row = await application(admin)
    key = await issue(admin, row['id'], scopes=['files:read'])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as guest:
        assert check(await guest.get('/health/live'), 200, 'public-live').json() == {'status': 'ok'}
        ready = await guest.get('/health/ready')
        assert ready.status_code in (200, 503) and set(ready.json()) <= {'status', 'component'}
        for path in (
            '/api/auth/me',
            '/api/auth/profile',
            '/api/models',
            '/api/usage',
            '/api/billing/me',
            '/api/developer/applications/scope-catalog',
        ):
            check(
                await guest.get(path, headers={'authorization': 'Bearer ' + key['api_key']}),
                401,
                'bearer-only-console-' + path,
            )
