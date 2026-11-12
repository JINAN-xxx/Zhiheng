"""Built-in roles describe administrator identity without becoming a grant path."""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from zhiheng.modules.identity import public
from zhiheng.modules.identity.admin_orm import IdentityRole, IdentityUserRole
from zhiheng.modules.identity.admin_permissions import PERMISSION_CODES
from zhiheng.modules.identity.builtin_roles import ensure_builtin_roles
from zhiheng.modules.identity.schema import schema_tables
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


@pytest_asyncio.fixture
async def identities() -> AsyncIterator[tuple[AsyncSession, public.IdentityService, public.IdentityAdminService]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for table in schema_tables():
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        identity = public.IdentityService()
        yield session, identity, public.IdentityAdminService(identity)
    await engine.dispose()


async def _root_and_role(
    session: AsyncSession,
    identity: public.IdentityService,
    admin: public.IdentityAdminService,
) -> tuple[Principal, dict[str, Any]]:
    root = await identity.create_user(session, username='root', password='a-long-test-password', is_admin=True)
    roles = (await admin.list(session, root, 'roles'))['items']
    assert len(roles) == 1, 'creating an administrator must also create its built-in role'
    assert roles[0]['system_key'] == 'super_admin'
    return root, roles[0]


async def test_cli_admin_has_real_builtin_membership_and_role_metadata(identities: tuple[Any, ...]) -> None:
    session, identity, admin = identities
    root, role = await _root_and_role(session, identity, admin)
    assert role['name'] == '超级管理员'
    assert role['permissions'] == []
    assert role['is_active'] is True
    assert role['member_count'] == 1
    assert (await admin.get(session, root, 'users', root.id))['role_ids'] == [role['id']]
    options = await admin.user_options(session, root)
    assert options['roles'] == [{'id': role['id'], 'name': '超级管理员', 'system_key': 'super_admin'}]


async def test_builtin_ensure_preserves_ordinary_roles_and_is_idempotent(identities: tuple[Any, ...]) -> None:
    session, identity, admin = identities
    regular = IdentityRole(code='super_admin', name='Existing role', permissions=['system:users:read'])
    second = IdentityRole(code='system_super_admin', name='Another existing role', permissions=[])
    session.add_all([regular, second])
    await session.flush()
    root = await identity.create_user(session, username='root', password='a-long-test-password', is_admin=True)
    another = await identity.create_user(
        session, username='other-admin', password='a-long-test-password', is_admin=True
    )
    rows = (await admin.list(session, root, 'roles'))['items']
    builtin = next((row for row in rows if row.get('system_key') == 'super_admin'), None)
    assert builtin is not None, 'a conflicting ordinary code must never prevent or replace the built-in role'
    assert builtin['code'] == 'system_super_admin_2'
    assert builtin['member_count'] == 2
    assert regular.name == 'Existing role' and regular.permissions == ['system:users:read']
    assert regular.system_key is None and second.system_key is None
    first = await ensure_builtin_roles(session)
    assert (await ensure_builtin_roles(session)).id == first.id == builtin['id']
    assert (await admin.get(session, root, 'users', another.id))['role_ids'] == [builtin['id']]
    assert (await admin.get(session, root, 'roles', regular.id))['member_count'] == 0


@pytest.mark.parametrize(
    'fields',
    [
        {'name': 'Renamed'},
        {'is_active': False},
        {'permissions': ['system:users:read']},
        {'remark': 'Changed'},
    ],
)
async def test_builtin_role_cannot_be_edited(identities: tuple[Any, ...], fields: dict[str, Any]) -> None:
    session, identity, admin = identities
    root, role = await _root_and_role(session, identity, admin)
    with pytest.raises(AppError) as protected:
        await admin.update(session, root, 'roles', role['id'], fields, expected_version=1)
    assert protected.value.code == 'builtin_role_protected'
    assert protected.value.status_code == 409
    assert (await admin.get(session, root, 'roles', role['id'])) == role


async def test_builtin_role_cannot_be_deleted_even_without_members(identities: tuple[Any, ...]) -> None:
    session, identity, admin = identities
    root, role = await _root_and_role(session, identity, admin)
    await session.execute(IdentityUserRole.__table__.delete())
    with pytest.raises(AppError) as protected:
        await admin.delete(session, root, 'roles', role['id'], expected_version=1)
    assert protected.value.code == 'builtin_role_protected'
    assert protected.value.status_code == 409


@pytest.mark.parametrize('include_old_system_id', [False, True])
async def test_admin_profile_and_demotion_sync_builtin_and_preserve_other_memberships(
    identities: tuple[Any, ...],
    include_old_system_id: bool,
) -> None:
    session, identity, admin = identities
    root, builtin = await _root_and_role(session, identity, admin)
    regular = await admin.create(session, root, 'roles', {'code': 'reader', 'name': 'Reader'})
    user = await admin.create(
        session,
        root,
        'users',
        {
            'username': 'other-admin',
            'password': 'a-long-test-password',
            'is_admin': True,
            'role_ids': [regular['id']],
        },
    )
    assert set(user['role_ids']) == {regular['id'], builtin['id']}
    _, token = await identity.login(session, username='other-admin', password='a-long-test-password')
    updated = await admin.update(
        session,
        root,
        'users',
        user['id'],
        {
            'display_name': 'New profile',
            'is_admin': True,
            'role_ids': user['role_ids'] if include_old_system_id else [regular['id']],
        },
        expected_version=1,
    )
    assert set(updated['role_ids']) == {regular['id'], builtin['id']}
    assert (await identity.authenticate(session, token)).is_admin is True
    demoted = await admin.update(
        session,
        root,
        'users',
        user['id'],
        {
            'is_admin': False,
            'role_ids': updated['role_ids'] if include_old_system_id else [regular['id']],
        },
        expected_version=2,
    )
    assert demoted['role_ids'] == [regular['id']]
    assert (await admin.get(session, root, 'roles', builtin['id']))['member_count'] == 1
    with pytest.raises(AppError):
        await identity.authenticate(session, token)


@pytest.mark.parametrize('send_role_ids', [False, True])
async def test_promotion_and_demotion_without_system_id_sync_membership(
    identities: tuple[Any, ...],
    send_role_ids: bool,
) -> None:
    session, identity, admin = identities
    root, builtin = await _root_and_role(session, identity, admin)
    regular = await admin.create(session, root, 'roles', {'code': 'reader', 'name': 'Reader'})
    user = await admin.create(
        session,
        root,
        'users',
        {
            'username': 'member',
            'password': 'a-long-test-password',
            'role_ids': [regular['id']],
        },
    )
    assignments = {'role_ids': [regular['id']]} if send_role_ids else {}
    promoted = await admin.update(
        session, root, 'users', user['id'], {'is_admin': True, **assignments}, expected_version=1
    )
    assert set(promoted['role_ids']) == {regular['id'], builtin['id']}
    demoted = await admin.update(
        session, root, 'users', user['id'], {'is_admin': False, **assignments}, expected_version=2
    )
    assert demoted['role_ids'] == [regular['id']]


@pytest.mark.parametrize('operation', ['create', 'update'])
@pytest.mark.parametrize('delegated', [False, True])
async def test_non_admin_identity_cannot_be_granted_builtin_id(
    identities: tuple[Any, ...],
    operation: str,
    delegated: bool,
) -> None:
    session, identity, admin = identities
    root, builtin = await _root_and_role(session, identity, admin)
    actor = Principal(root.id, 'delegated', False, PERMISSION_CODES) if delegated else root
    if operation == 'create':
        with pytest.raises(AppError) as forbidden:
            await admin.create(
                session,
                actor,
                'users',
                {
                    'username': 'member',
                    'password': 'a-long-test-password',
                    'role_ids': [builtin['id']],
                },
            )
    else:
        user = await admin.create(session, root, 'users', {'username': 'member', 'password': 'a-long-test-password'})
        with pytest.raises(AppError) as forbidden:
            await admin.update(session, actor, 'users', user['id'], {'role_ids': [builtin['id']]}, expected_version=1)
    assert forbidden.value.status_code == 403
    assert forbidden.value.code == 'builtin_role_assignment_forbidden'


async def test_builtin_permissions_never_authorize_non_admin_even_if_manually_linked(
    identities: tuple[Any, ...],
) -> None:
    session, identity, admin = identities
    root, builtin = await _root_and_role(session, identity, admin)
    member = await identity.create_user(session, username='member', password='a-long-test-password')
    await session.execute(
        update(IdentityRole).where(IdentityRole.id == builtin['id']).values(permissions=sorted(PERMISSION_CODES))
    )
    session.add(IdentityUserRole(user_id=member.id, role_id=builtin['id']))
    await session.flush()
    current = await identity.principal_for_user(session, member.id)
    assert current.is_admin is False and current.permissions == frozenset()
    operator = Principal(member.id, 'operator', False, PERMISSION_CODES)
    assert (await admin.user_options(session, operator))['roles'] == []
    assert (await admin.get(session, root, 'users', member.id))['role_ids'] == [builtin['id']]


async def test_builtin_payload_does_not_skip_other_assignment_validation(identities: tuple[Any, ...]) -> None:
    session, identity, admin = identities
    builtin = IdentityRole(id=UUID(int=1), code='super_admin', system_key='super_admin', name='超级管理员')
    inactive = IdentityRole(id=UUID(int=2), code='inactive', name='Inactive role', is_active=False)
    session.add_all([builtin, inactive])
    await session.flush()
    root = await identity.create_user(session, username='root', password='a-long-test-password', is_admin=True)
    with pytest.raises(AppError) as invalid:
        await admin.create(
            session,
            root,
            'users',
            {
                'username': 'other-admin',
                'password': 'a-long-test-password',
                'is_admin': True,
                'role_ids': [builtin.id, inactive.id],
            },
        )
    assert invalid.value.code == 'inactive_assignment'
