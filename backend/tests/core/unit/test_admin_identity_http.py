from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from zhiheng.api.dependencies import get_container, get_principal, get_session
from zhiheng.api.errors import install_error_handlers
from zhiheng.api.routes.admin_identity import router
from zhiheng.application.identity.accounts import AccountService
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.modules.identity.public import IdentityAdminService, IdentityService, schema_tables
from zhiheng.modules.skills.public import SkillService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class SkillInitializerSpy(SkillService):
    def __init__(self) -> None:
        self.calls: list[tuple[AsyncSession, UUID]] = []

    async def initialize_owner(self, session: AsyncSession, owner_id: UUID) -> None:
        assert session.in_transaction(), 'personal resources must share the account transaction'
        assert await session.get(IdentityUser, owner_id) is not None
        self.calls.append((session, owner_id))


class Audit:
    def __init__(self, initializer: SkillInitializerSpy) -> None:
        self.initializer = initializer
        self.calls: list[dict[str, Any]] = []
        self.fail = False

    async def record(self, session: AsyncSession, **fields: Any) -> None:
        assert session.in_transaction(), 'audit must share the domain transaction'
        if fields['action'] == 'users.create':
            initialized_session, owner_id = self.initializer.calls[-1]
            assert initialized_session is session
            assert str(owner_id) == fields['resource_id']
        if self.fail:
            raise AppError('audit_failed', 'audit test failure', 503)
        self.calls.append(fields)


@pytest_asyncio.fixture
async def api() -> AsyncIterator[tuple[httpx.AsyncClient, Audit, Any, Any]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for table in schema_tables():
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService()
    async with factory.begin() as session:
        principal = await identity.create_user(session, username='root', password='a-long-test-password', is_admin=True)
    initializer = SkillInitializerSpy()
    audit = Audit(initializer)
    admin_identity = IdentityAdminService(identity)
    container = SimpleNamespace(
        identity=identity,
        admin_identity=admin_identity,
        accounts=AccountService(identity, initializer, admin_identity),
        operations=audit,
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_container] = lambda: container
    app.dependency_overrides[get_principal] = lambda: principal

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client, audit, factory, app
    await engine.dispose()


async def test_http_writes_validate_versions_and_log_without_password(api: tuple[Any, ...]) -> None:
    client, audit, _, _ = api
    created = await client.post('/api/admin/users', json={'username': 'member', 'password': 'a-long-test-password'})
    assert created.status_code == 201, created.text
    user = created.json()
    assert user['version'] == 1 and 'password' not in str(user)
    assert [owner_id for _, owner_id in audit.initializer.calls] == [UUID(user['id'])]
    assert audit.calls[0]['action'] == 'users.create'
    assert 'a-long-test-password' not in str(audit.calls)
    missing = await client.patch(f'/api/admin/users/{user["id"]}', json={'display_name': 'test'})
    assert missing.status_code == 422
    changed = await client.patch(f'/api/admin/users/{user["id"]}', json={'display_name': 'test', 'expected_version': 1})
    assert changed.status_code == 200 and changed.json()['version'] == 2
    stale = await client.delete(f'/api/admin/users/{user["id"]}?expected_version=1')
    assert stale.status_code == 409
    deleted = await client.delete(f'/api/admin/users/{user["id"]}?expected_version=2')
    assert deleted.status_code == 204
    assert [call['action'] for call in audit.calls] == ['users.create', 'users.update', 'users.delete']


async def test_failed_audit_rolls_back_user_creation(api: tuple[Any, ...]) -> None:
    client, audit, factory, _ = api
    audit.fail = True
    response = await client.post('/api/admin/users', json={'username': 'rollback', 'password': 'a-long-test-password'})
    assert response.status_code == 503
    async with factory() as session:
        assert await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'rollback')) is None


async def test_invalid_fields_are_rejected_without_echoing_password(api: tuple[Any, ...]) -> None:
    client, _, _, _ = api
    response = await client.post(
        '/api/admin/users', json={'username': 'member', 'password': 'private', 'extra': 'field'}
    )
    assert response.status_code == 422 and 'private' not in response.text
    created = await client.post('/api/admin/users', json={'username': 'member', 'password': 'a-long-test-password'})
    identifier = created.json()['id']
    for fields in ({'username': 'renamed'}, {'password': 'a-new-password'}, {'is_active': None}):
        response = await client.patch(f'/api/admin/users/{identifier}', json={**fields, 'expected_version': 1})
        assert response.status_code == 422, response.text


async def test_identity_router_leaves_operations_paths_available(api: tuple[Any, ...]) -> None:
    client, _, _, _ = api
    assert (await client.get('/api/admin/dictionaries')).status_code == 404
    response = await client.get('/api/admin/users/options')
    assert response.status_code == 200 and set(response.json()) == {'roles', 'posts', 'departments'}


async def test_role_codes_are_generated_uniquely_and_remain_stable_after_edit(api: tuple[Any, ...]) -> None:
    client, _, _, _ = api
    codes = []
    for name in ('First role', 'Second role'):
        created = await client.post('/api/admin/roles', json={'name': name})
        assert created.status_code == 201, created.text
        role = created.json()
        code = role['code']
        assert len(code) == 37 and code.startswith('role_')
        assert UUID(hex=code[5:]).version == 4
        updated = await client.patch(
            f'/api/admin/roles/{role["id"]}', json={'name': 'Renamed role', 'expected_version': 1}
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()['code'] == code
        codes.append(code)
    assert len(set(codes)) == 2


async def test_explicit_legacy_role_code_remains_supported_unique_and_immutable(api: tuple[Any, ...]) -> None:
    client, _, _, _ = api
    fields = {'name': 'Legacy role', 'code': 'legacy.role:reader-1'}
    created = await client.post('/api/admin/roles', json=fields)
    assert created.status_code == 201, created.text
    role = created.json()
    duplicate = await client.post('/api/admin/roles', json=fields)
    assert duplicate.status_code == 409
    updated = await client.patch(
        f'/api/admin/roles/{role["id"]}', json={'name': 'Renamed legacy role', 'expected_version': 1}
    )
    assert updated.status_code == 200 and updated.json()['code'] == fields['code']
    rejected = await client.patch(f'/api/admin/roles/{role["id"]}', json={'code': 'renamed', 'expected_version': 2})
    assert rejected.status_code == 422
    assert (await client.get(f'/api/admin/roles/{role["id"]}')).json()['code'] == fields['code']


@pytest.mark.parametrize('code', ['', '   ', None, 'invalid code'])
async def test_explicit_invalid_role_codes_are_rejected(api: tuple[Any, ...], code: str | None) -> None:
    client, _, _, _ = api
    response = await client.post('/api/admin/roles', json={'name': 'Invalid role', 'code': code})
    assert response.status_code == 422


async def test_ai_only_permissions_do_not_open_the_system_permission_directory(api: tuple[Any, ...]) -> None:
    client, _, _, app = api
    app.dependency_overrides[get_principal] = lambda: Principal(
        uuid4(), 'ai-operator', False, frozenset({'ai:models:write', 'ai:storage:read'})
    )
    for path in ('/api/admin/permissions', '/api/admin/menus?navigation=true', '/api/admin/roles'):
        response = await client.get(path)
        assert response.status_code == 403, response.text
