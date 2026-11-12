"""Retired features must stay unreachable even to administrators."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import update

from tests.core.unit.test_admin_identity import context as _identity_context
from tests.core.unit.test_admin_identity_http import api as _identity_api
from zhiheng.api.console.admin_operations import router as operations_router
from zhiheng.bootstrap.schema import schema_metadata
from zhiheng.modules.identity.admin_contracts import UserCreate, UserPatch
from zhiheng.modules.identity.admin_orm import IdentityRole
from zhiheng.modules.identity.admin_permissions import PERMISSION_CODES, permission_catalog
from zhiheng.modules.identity.profile_contracts import ProfileInfo
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

context = _identity_context
api = _identity_api

RETIRED = [f'system:{area}:{action}' for area in ('menus', 'posts', 'dictionaries') for action in ('read', 'write')]


async def test_retired_routes_are_unregistered_for_all_methods(api: tuple[Any, ...]) -> None:
    client, _, _, app = api
    app.include_router(operations_router)
    identifier = str(uuid4())
    paths = [
        f'/api/admin/{area}{suffix}' for area in ('menus', 'posts', 'dictionaries') for suffix in ('', '/' + identifier)
    ]
    paths += [f'/api/admin/dictionaries/{identifier}/items', f'/api/admin/dictionaries/{identifier}/items/{identifier}']
    for path in paths:
        for method in ('GET', 'POST', 'PATCH', 'DELETE'):
            response = await client.request(method, path, json={'expected_version': 1})
            assert response.status_code == 404, (method, path, response.text)
    assert (await client.get('/api/admin/menus?navigation=true')).status_code == 404
    schema = app.openapi()
    assert not any(
        path.startswith(('/api/admin/menus', '/api/admin/posts', '/api/admin/dictionaries')) for path in schema['paths']
    )


def test_retired_resources_are_absent_from_active_metadata_and_contracts() -> None:
    archived = {
        'identity_menus',
        'identity_posts',
        'identity_user_posts',
        'operations_dictionaries',
        'operations_dictionary_items',
    }
    assert archived.isdisjoint(schema_metadata().tables)
    assert set(RETIRED).isdisjoint(PERMISSION_CODES)
    assert set(RETIRED).isdisjoint(item['code'] for item in permission_catalog())
    assert 'post_ids' not in UserCreate.model_fields and 'post_ids' not in UserPatch.model_fields
    assert 'posts' not in ProfileInfo.model_fields


@pytest.mark.parametrize('code', RETIRED)
async def test_retired_codes_cannot_be_granted_to_new_roles(context: tuple[Any, ...], code: str) -> None:
    session, _, service, admin = context
    with pytest.raises(AppError) as denied:
        await service.create(session, admin, 'roles', {'name': 'Unsupported grant', 'permissions': [code]})
    assert denied.value.code == 'unknown_permission'


async def test_historical_role_codes_do_not_authorize_or_block_valid_edits(context: tuple[Any, ...]) -> None:
    session, identity, service, admin = context
    role = await service.create(
        session, admin, 'roles', {'name': 'Legacy reader', 'permissions': ['system:users:read']}
    )
    legacy = ['system:users:read', *RETIRED]
    await session.execute(update(IdentityRole).where(IdentityRole.id == role['id']).values(permissions=legacy))
    user = await service.create(
        session,
        admin,
        'users',
        {'username': 'legacy-reader', 'password': 'a-long-test-password', 'role_ids': [role['id']]},
    )
    principal, token = await identity.login(session, username='legacy-reader', password='a-long-test-password')
    assert principal.permissions == frozenset({'system:users:read'})
    assert (await service.get(session, admin, 'roles', role['id']))['permissions'] == ['system:users:read']
    assert 'post_ids' not in user
    assert set(await service.user_options(session, admin)) == {'roles', 'departments'}
    assert 'posts' not in (await identity.profile(session, user_id=user['id'])).model_dump()

    # Delegated user administration still works when an assigned role contains dormant codes.
    manager = Principal(admin.id, 'manager', False, frozenset({'system:users:write', 'system:users:read'}))
    updated = await service.update(
        session, manager, 'users', user['id'], {'display_name': 'Updated'}, expected_version=1
    )
    assert updated['display_name'] == 'Updated'
    # A cached editor may resubmit the old codes that are already on this role.
    changed = await service.update(session, admin, 'roles', role['id'], {'permissions': legacy}, expected_version=1)
    assert changed['permissions'] == ['system:users:read']
    assert (await identity.authenticate(session, token)).permissions == frozenset({'system:users:read'})
    with pytest.raises(AppError):
        await service.update(
            session, admin, 'roles', role['id'], {'permissions': ['system:posts:write']}, expected_version=2
        )


async def test_legacy_only_role_keeps_login_without_retired_authority(context: tuple[Any, ...]) -> None:
    session, identity, service, admin = context
    role = await service.create(session, admin, 'roles', {'name': 'Dormant legacy role'})
    await session.execute(update(IdentityRole).where(IdentityRole.id == role['id']).values(permissions=RETIRED))
    await service.create(
        session,
        admin,
        'users',
        {'username': 'legacy-only', 'password': 'a-long-test-password', 'role_ids': [role['id']]},
    )
    principal, token = await identity.login(session, username='legacy-only', password='a-long-test-password')
    assert principal.permissions == frozenset()
    assert (await identity.authenticate(session, token)).permissions == frozenset()


async def test_user_http_rejects_retired_assignment_fields_without_partial_update(api: tuple[Any, ...]) -> None:
    client, _, _, _ = api
    created = await client.post(
        '/api/admin/users', json={'username': 'preserved-user', 'password': 'a-long-test-password'}
    )
    assert created.status_code == 201
    user = created.json()
    assert 'post_ids' not in user
    response = await client.patch(
        '/api/admin/users/' + user['id'], json={'expected_version': 1, 'display_name': 'Must not save', 'post_ids': []}
    )
    assert response.status_code == 422
    current = (await client.get('/api/admin/users/' + user['id'])).json()
    assert current == user
