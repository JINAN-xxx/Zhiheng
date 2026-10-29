from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from zhiheng.modules.identity import public
from zhiheng.modules.identity.admin_contracts import RoleCreate
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

AI_PERMISSION_CODES = frozenset(
    {
        'ai:models:read',
        'ai:models:write',
        'ai:models:test',
        'ai:rates:read',
        'ai:rates:write',
        'ai:skills:write',
        'ai:storage:read',
        'ai:storage:write',
    }
)


def test_admin_service_is_public() -> None:
    assert hasattr(public, 'IdentityAdminService'), 'identity must expose real admin use cases'


def test_role_create_defaults_to_unique_uuid4_codes() -> None:
    codes = [RoleCreate.model_validate({'name': 'Generated role'}).code for _ in range(2)]
    assert len(set(codes)) == 2
    assert all(len(code) == 37 and code.startswith('role_') and UUID(hex=code[5:]).version == 4 for code in codes)


@pytest_asyncio.fixture
async def context() -> AsyncIterator[tuple[AsyncSession, object, object, Principal]]:
    assert hasattr(public, 'IdentityAdminService'), 'identity admin service is not implemented'
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for table in public.schema_tables():
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    identity = public.IdentityService()
    admin = public.IdentityAdminService(identity)
    async with factory() as session:
        principal = await identity.create_user(session, username='root', password='a-long-test-password', is_admin=True)
        await session.commit()
        yield session, identity, admin, principal
    await engine.dispose()


async def test_roles_apply_to_existing_sessions_and_disabled_roles_stop_authorizing(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, identity, admin, root = context
    role = await admin.create(
        session,
        root,
        'roles',
        {
            'code': 'reader',
            'name': 'Reader',
            'permissions': ['system:users:read'],
        },
    )
    user = await admin.create(
        session,
        root,
        'users',
        {
            'username': 'reader',
            'password': 'a-long-test-password',
            'role_ids': [role['id']],
        },
    )
    principal, token = await identity.login(session, username='reader', password='a-long-test-password')
    assert principal.permissions == frozenset({'system:users:read'})
    await admin.update(session, root, 'roles', role['id'], {'is_active': False}, expected_version=1)
    current = await identity.authenticate(session, token)
    assert current.permissions == frozenset()
    assert (await identity.principal_for_user(session, user['id'])).permissions == frozenset()


async def test_users_are_versioned_and_deleted_without_destroying_identity(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, identity, admin, root = context
    user = await admin.create(
        session,
        root,
        'users',
        {
            'username': 'member',
            'password': 'a-long-test-password',
            'display_name': '成员',
        },
    )
    _, token = await identity.login(session, username='member', password='a-long-test-password')
    updated = await admin.update(session, root, 'users', user['id'], {'display_name': '新成员'}, expected_version=1)
    assert updated['version'] == 2
    with pytest.raises(AppError) as stale:
        await admin.update(session, root, 'users', user['id'], {'display_name': '覆盖'}, expected_version=1)
    assert stale.value.status_code == 409
    await admin.delete(session, root, 'users', user['id'], expected_version=2)
    assert await session.get(IdentityUser, user['id']) is not None
    with pytest.raises(AppError) as revoked:
        await identity.authenticate(session, token)
    assert revoked.value.status_code == 401
    assert (await admin.list(session, root, 'users'))['total'] == 1
    assert user['id'] in await identity.matching_user_ids(session, search='member')


async def test_self_disable_delete_and_admin_demotion_are_rejected(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, _, admin, root = context
    for fields in ({'is_active': False}, {'is_admin': False}):
        with pytest.raises(AppError) as protected:
            await admin.update(session, root, 'users', root.id, fields, expected_version=1)
        assert protected.value.status_code == 409
    with pytest.raises(AppError):
        await admin.delete(session, root, 'users', root.id, expected_version=1)


async def test_non_admin_cannot_grant_higher_permissions_or_manage_super_admin(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, identity, admin, root = context
    role = await admin.create(
        session,
        root,
        'roles',
        {
            'code': 'manager',
            'name': 'Manager',
            'permissions': ['system:users:write', 'system:roles:write'],
        },
    )
    user = await admin.create(
        session,
        root,
        'users',
        {
            'username': 'manager',
            'password': 'a-long-test-password',
            'role_ids': [role['id']],
        },
    )
    manager = await identity.principal_for_user(session, user['id'])
    with pytest.raises(AppError) as escalation:
        await admin.create(
            session,
            manager,
            'roles',
            {
                'code': 'escalation',
                'name': 'Escalation',
                'permissions': ['system:logs:read'],
            },
        )
    assert escalation.value.status_code == 403
    with pytest.raises(AppError):
        await admin.update(session, manager, 'users', root.id, {'display_name': 'hijacked'}, expected_version=1)
    with pytest.raises(AppError):
        await admin.create(
            session,
            manager,
            'users',
            {
                'username': 'superuser',
                'password': 'a-long-test-password',
                'is_admin': True,
            },
        )


async def test_department_cycles_and_occupied_resources_are_protected(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, _, admin, root = context
    department = await admin.create(session, root, 'departments', {'name': '总部'})
    child = await admin.create(session, root, 'departments', {'name': '分部', 'parent_id': department['id']})
    with pytest.raises(AppError):
        await admin.update(
            session, root, 'departments', department['id'], {'parent_id': child['id']}, expected_version=1
        )
    with pytest.raises(AppError):
        await admin.delete(session, root, 'departments', department['id'], expected_version=1)
    post = await admin.create(session, root, 'posts', {'code': 'engineer', 'name': '工程师'})
    await admin.create(
        session,
        root,
        'users',
        {
            'username': 'employee',
            'password': 'a-long-test-password',
            'department_id': child['id'],
            'post_ids': [post['id']],
        },
    )
    for kind, identifier in [('departments', child['id']), ('posts', post['id'])]:
        with pytest.raises(AppError) as occupied:
            await admin.delete(session, root, kind, identifier, expected_version=1)
        assert occupied.value.status_code == 409


async def test_password_reset_revokes_sessions_and_dto_never_contains_password(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, identity, admin, root = context
    user = await admin.create(session, root, 'users', {'username': 'member', 'password': 'a-long-test-password'})
    _, token = await identity.login(session, username='member', password='a-long-test-password')
    result = await admin.reset_password(session, root, user['id'], 'a-new-test-password', expected_version=1)
    assert 'password' not in str(result) and 'hash' not in str(result)
    with pytest.raises(AppError):
        await identity.authenticate(session, token)
    await identity.login(session, username='member', password='a-new-test-password')


async def test_sessions_expose_only_public_ids_and_current_session_cannot_be_kicked(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, identity, admin, root = context
    _, token = await identity.login(session, username='root', password='a-long-test-password')
    listing = await admin.list_sessions(session, root, current_token=token)
    current = listing['items'][0]
    assert current['is_current'] is True
    assert isinstance(current['session_id'], UUID)
    assert token not in str(listing) and identity._hash_token(token) not in str(listing)
    with pytest.raises(AppError):
        await admin.revoke_session(session, root, current['session_id'], current_token=token, expected_version=1)
    _, other_token = await identity.login(session, username='root', password='a-long-test-password')
    other = next(
        row for row in (await admin.list_sessions(session, root, current_token=token))['items'] if not row['is_current']
    )
    await admin.revoke_session(session, root, other['session_id'], current_token=token, expected_version=1)
    with pytest.raises(AppError):
        await identity.authenticate(session, other_token)


async def test_menu_paths_and_permission_catalog_are_fixed(context: tuple[AsyncSession, Any, Any, Principal]) -> None:
    session, _, admin, root = context
    with pytest.raises(AppError):
        await admin.create(session, root, 'menus', {'name': 'unsafe', 'path': 'https://evil.example'})
    with pytest.raises(AppError):
        await admin.create(session, root, 'menus', {'name': 'unknown', 'path': '/does-not-exist'})
    with pytest.raises(AppError):
        await admin.create(session, root, 'roles', {'code': 'bad', 'name': 'bad', 'permissions': ['system:root:write']})
    menu = await admin.create(
        session,
        root,
        'menus',
        {
            'name': '用户',
            'path': '/admin/users',
            'permission': 'system:users:read',
        },
    )
    assert menu['path'] == '/admin/users'
    catalog = public.permission_catalog()
    system = [item for item in catalog if item['code'].startswith('system:')]
    ai = [item for item in catalog if item['code'].startswith('ai:')]
    assert len(system) == 20
    assert {item['code'] for item in ai} == AI_PERMISSION_CODES
    assert len(catalog) == len({item['code'] for item in catalog}) == 28
    assert all(item['group'].startswith('系统管理 · ') for item in system)
    assert {item['group'] for item in ai} == {
        'AI平台 · 模型与凭据',
        'AI平台 · 费率',
        'AI平台 · 共享技能',
        'AI平台 · 存储治理',
    }
    assert all(item['label'] for item in catalog)


async def test_roles_can_create_and_update_with_the_full_permission_catalog(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, _, admin, root = context
    permissions = sorted({item['code'] for item in public.permission_catalog()} | AI_PERMISSION_CODES)
    role = await admin.create(
        session, root, 'roles', {'code': 'all-permissions', 'name': 'All permissions', 'permissions': permissions}
    )
    assert role['permissions'] == permissions
    updated = await admin.update(session, root, 'roles', role['id'], {'permissions': permissions}, expected_version=1)
    assert updated['permissions'] == permissions


@pytest.mark.parametrize('revoke', ['disable_role', 'remove_permissions', 'remove_membership'])
async def test_ai_role_grants_and_revocations_refresh_existing_sessions(
    context: tuple[AsyncSession, Any, Any, Principal],
    revoke: str,
) -> None:
    session, identity, admin, root = context
    role = await admin.create(session, root, 'roles', {'code': 'ai-operator', 'name': 'AI operator'})
    user = await admin.create(
        session,
        root,
        'users',
        {'username': 'ai-operator', 'password': 'a-long-test-password', 'role_ids': [role['id']]},
    )
    before, token = await identity.login(session, username='ai-operator', password='a-long-test-password')
    assert before.permissions == frozenset()
    await admin.update(
        session, root, 'roles', role['id'], {'permissions': sorted(AI_PERMISSION_CODES)}, expected_version=1
    )
    current = await identity.authenticate(session, token)
    assert current.permissions == AI_PERMISSION_CODES
    assert current.is_admin is False
    assert (await identity.principal_for_user(session, user['id'])).permissions == AI_PERMISSION_CODES
    if revoke == 'remove_membership':
        await admin.update(session, root, 'users', user['id'], {'role_ids': []}, expected_version=1)
    else:
        changes = {'is_active': False} if revoke == 'disable_role' else {'permissions': []}
        await admin.update(session, root, 'roles', role['id'], changes, expected_version=2)
    assert (await identity.authenticate(session, token)).permissions == frozenset()
    assert (await identity.principal_for_user(session, user['id'])).permissions == frozenset()


async def test_delegated_ai_grants_require_held_permissions_and_cannot_elevate_self(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, identity, admin, root = context
    held = {'system:roles:write', 'system:users:write', 'ai:models:read'}
    manager_role = await admin.create(
        session, root, 'roles', {'code': 'ai-manager', 'name': 'AI manager', 'permissions': sorted(held)}
    )
    user = await admin.create(
        session,
        root,
        'users',
        {'username': 'ai-manager', 'password': 'a-long-test-password', 'role_ids': [manager_role['id']]},
    )
    manager = await identity.principal_for_user(session, user['id'])
    granted = await admin.create(
        session,
        manager,
        'roles',
        {'code': 'ai-reader', 'name': 'AI reader', 'permissions': ['ai:models:read']},
    )
    assert granted['permissions'] == ['ai:models:read']
    powerful = await admin.create(
        session, root, 'roles', {'code': 'ai-writer', 'name': 'AI writer', 'permissions': ['ai:models:write']}
    )
    with pytest.raises(AppError) as grant:
        await admin.create(
            session,
            manager,
            'roles',
            {'code': 'forbidden', 'name': 'Forbidden', 'permissions': ['ai:models:write']},
        )
    assert grant.value.status_code == 403
    with pytest.raises(AppError) as elevate_role:
        await admin.update(
            session,
            manager,
            'roles',
            manager_role['id'],
            {'permissions': sorted(held | {'ai:models:write'})},
            expected_version=1,
        )
    assert elevate_role.value.status_code == 403
    with pytest.raises(AppError) as elevate_user:
        await admin.update(
            session,
            manager,
            'users',
            user['id'],
            {'role_ids': [manager_role['id'], powerful['id']]},
            expected_version=1,
        )
    assert elevate_user.value.status_code == 403
    assert (await identity.principal_for_user(session, user['id'])).permissions == frozenset(held)


async def test_ai_permissions_cannot_access_system_administration(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, _, admin, root = context
    operator = Principal(root.id, 'ai-operator', False, AI_PERMISSION_CODES)
    with pytest.raises(AppError) as navigation:
        await admin.navigation(session, operator)
    assert navigation.value.status_code == 403
    with pytest.raises(AppError) as listing:
        await admin.list(session, operator, 'roles')
    assert listing.value.status_code == 403


async def test_read_permission_is_required_independently_from_write(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, _, admin, root = context
    member = Principal(root.id, root.username, False, frozenset({'system:users:write'}))
    with pytest.raises(AppError) as forbidden:
        await admin.list(session, member, 'users')
    assert forbidden.value.status_code == 403


async def test_navigation_is_filtered_by_fixed_permissions_and_options_prevent_escalation(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, _, admin, root = context
    await admin.create(
        session, root, 'roles', {'code': 'powerful', 'name': '高权', 'permissions': ['system:roles:write']}
    )
    member = Principal(root.id, root.username, False, frozenset({'system:users:read'}))
    menus = await admin.navigation(session, member)
    assert {row['path'] for row in menus['items']} == {'/admin/users'}
    options = await admin.user_options(session, member)
    assert options['roles'] == []


async def test_password_reset_cannot_take_over_more_powerful_regular_user(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, _identity, admin, root = context
    role = await admin.create(
        session,
        root,
        'roles',
        {
            'code': 'audit',
            'name': '审计',
            'permissions': ['system:logs:read'],
        },
    )
    user = await admin.create(
        session,
        root,
        'users',
        {
            'username': 'auditor',
            'password': 'a-long-test-password',
            'role_ids': [role['id']],
        },
    )
    manager = Principal(root.id, 'delegated', False, frozenset({'system:users:write'}))
    with pytest.raises(AppError) as forbidden:
        await admin.reset_password(session, manager, user['id'], 'takeover-password', expected_version=1)
    assert forbidden.value.status_code == 403


def test_http_router_is_available() -> None:
    import importlib.util

    assert importlib.util.find_spec('zhiheng.api.routes.admin_identity') is not None


async def test_principal_refresh_does_not_trust_cached_enabled_user(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, identity, admin, root = context
    user = await admin.create(session, root, 'users', {'username': 'cached', 'password': 'a-long-test-password'})
    cached_row = await session.get(IdentityUser, user['id'])
    await session.execute(
        update(IdentityUser)
        .where(IdentityUser.id == user['id'])
        .values(is_active=False)
        .execution_options(synchronize_session=False)
    )
    assert cached_row.is_active is True
    with pytest.raises(AppError):
        await identity.principal_for_user(session, user['id'])


@pytest.mark.parametrize('is_admin', [False, True])
async def test_profile_update_with_unchanged_admin_flag_preserves_session(
    context: tuple[AsyncSession, Any, Any, Principal],
    is_admin: bool,
) -> None:
    session, identity, admin, root = context
    if is_admin:
        identifier, username = root.id, root.username
    else:
        user = await admin.create(session, root, 'users', {'username': 'member', 'password': 'a-long-test-password'})
        identifier, username = user['id'], user['username']
    _, token = await identity.login(session, username=username, password='a-long-test-password')
    updated = await admin.update(
        session,
        root,
        'users',
        identifier,
        {'display_name': 'updated profile', 'is_active': True, 'is_admin': is_admin},
        expected_version=1,
    )
    assert updated['version'] == 2
    assert (await identity.authenticate(session, token)).id == identifier
    assert (
        await session.scalar(
            select(IdentitySession.id).where(
                IdentitySession.token_hash == identity._hash_token(token),
            )
        )
        is not None
    )


@pytest.mark.parametrize('mutation', ['promote', 'demote', 'disable'])
async def test_actual_admin_flag_change_or_deactivation_revokes_session(
    context: tuple[AsyncSession, Any, Any, Principal],
    mutation: str,
) -> None:
    session, identity, admin, root = context
    user = await admin.create(
        session,
        root,
        'users',
        {
            'username': 'member',
            'password': 'a-long-test-password',
            'is_admin': mutation == 'demote',
        },
    )
    _, token = await identity.login(session, username='member', password='a-long-test-password')
    fields = {'is_active': False} if mutation == 'disable' else {'is_admin': mutation == 'promote'}
    await admin.update(session, root, 'users', user['id'], fields, expected_version=1)
    assert (
        await session.scalar(
            select(IdentitySession.id).where(
                IdentitySession.token_hash == identity._hash_token(token),
            )
        )
        is None
    )
    with pytest.raises(AppError) as revoked:
        await identity.authenticate(session, token)
    assert revoked.value.status_code == 401


@pytest.mark.parametrize('deactivate_user', [False, True])
async def test_user_updates_can_retain_existing_inactive_assignments(
    context: tuple[AsyncSession, Any, Any, Principal],
    deactivate_user: bool,
) -> None:
    session, _, admin, root = context
    department = await admin.create(session, root, 'departments', {'name': 'Legacy department'})
    post = await admin.create(session, root, 'posts', {'code': 'legacy', 'name': 'Legacy post'})
    role = await admin.create(session, root, 'roles', {'code': 'legacy', 'name': 'Legacy role'})
    assignments = {'department_id': department['id'], 'post_ids': [post['id']], 'role_ids': [role['id']]}
    user = await admin.create(
        session,
        root,
        'users',
        {
            'username': 'member',
            'password': 'a-long-test-password',
            **assignments,
        },
    )
    for kind, item in [('departments', department), ('posts', post), ('roles', role)]:
        await admin.update(session, root, kind, item['id'], {'is_active': False}, expected_version=1)
    fields = {'is_active': False} if deactivate_user else {'display_name': 'updated profile'}
    updated = await admin.update(
        session,
        root,
        'users',
        user['id'],
        {
            **assignments,
            **fields,
        },
        expected_version=1,
    )
    assert updated['version'] == 2
    assert all(updated[key] == value for key, value in assignments.items())
    assert all(updated[key] == value for key, value in fields.items())


@pytest.mark.parametrize('kind,field', [('departments', 'department_id'), ('posts', 'post_ids'), ('roles', 'role_ids')])
@pytest.mark.parametrize('create_user', [False, True])
async def test_new_assignments_to_inactive_resources_are_rejected(
    context: tuple[AsyncSession, Any, Any, Principal],
    kind: str,
    field: str,
    create_user: bool,
) -> None:
    session, _, admin, root = context
    resource_fields = {'name': 'Inactive', 'is_active': False}
    if kind != 'departments':
        resource_fields['code'] = 'inactive'
    item = await admin.create(session, root, kind, resource_fields)
    assignment = {field: item['id'] if kind == 'departments' else [item['id']]}
    if create_user:
        with pytest.raises(AppError) as invalid:
            await admin.create(
                session,
                root,
                'users',
                {
                    'username': 'new-member',
                    'password': 'a-long-test-password',
                    **assignment,
                },
            )
    else:
        user = await admin.create(session, root, 'users', {'username': 'member', 'password': 'a-long-test-password'})
        with pytest.raises(AppError) as invalid:
            await admin.update(session, root, 'users', user['id'], assignment, expected_version=1)
    assert invalid.value.code in {'inactive_department', 'inactive_assignment'}


async def test_retaining_inactive_role_cannot_bypass_operator_permission_scope(
    context: tuple[AsyncSession, Any, Any, Principal],
) -> None:
    session, _, admin, root = context
    role = await admin.create(
        session,
        root,
        'roles',
        {
            'code': 'audit',
            'name': 'Audit',
            'permissions': ['system:logs:read'],
        },
    )
    user = await admin.create(
        session,
        root,
        'users',
        {
            'username': 'auditor',
            'password': 'a-long-test-password',
            'role_ids': [role['id']],
        },
    )
    await admin.update(session, root, 'roles', role['id'], {'is_active': False}, expected_version=1)
    operator = Principal(root.id, 'operator', False, frozenset({'system:users:write'}))
    with pytest.raises(AppError) as forbidden:
        await admin.update(
            session,
            operator,
            'users',
            user['id'],
            {
                'display_name': 'updated profile',
                'role_ids': [role['id']],
            },
            expected_version=1,
        )
    assert forbidden.value.status_code == 403


@pytest.mark.parametrize('path', ['/overview', '/chat', '/admin'])
@pytest.mark.parametrize('action', ['create', 'update'])
async def test_menu_configuration_rejects_paths_outside_admin_navigation(
    context: tuple[AsyncSession, Any, Any, Principal],
    path: str,
    action: str,
) -> None:
    session, _, admin, root = context
    if action == 'create':
        with pytest.raises(AppError) as invalid:
            await admin.create(session, root, 'menus', {'name': 'Unsupported', 'path': path})
    else:
        menu = await admin.create(session, root, 'menus', {'name': 'Users', 'path': '/admin/users'})
        with pytest.raises(AppError) as invalid:
            await admin.update(session, root, 'menus', menu['id'], {'path': path}, expected_version=1)
    assert invalid.value.code == 'invalid_menu_path'
