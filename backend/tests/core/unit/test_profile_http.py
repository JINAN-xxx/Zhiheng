from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from zhiheng.api.dependencies import get_container, get_principal, get_session
from zhiheng.api.errors import install_error_handlers
from zhiheng.api.routes.auth import router
from zhiheng.modules.identity.admin_orm import (
    IdentityDepartment,
    IdentityPost,
    IdentityRole,
    IdentityUserPost,
    IdentityUserRole,
)
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.modules.identity.public import IdentityService, schema_tables
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

PASSWORD = 'a-long-test-password'
NEW_PASSWORD = 'a-different-test-password'


class Audit:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = False

    async def record(self, session: AsyncSession, **fields: Any) -> None:
        assert session.in_transaction()
        if self.fail:
            raise AppError('audit_failed', 'audit test failure', 503)
        self.calls.append(fields)


@dataclass
class ProfileApi:
    client: httpx.AsyncClient
    audit: Audit
    factory: async_sessionmaker[AsyncSession]
    member: Principal


@pytest_asyncio.fixture
async def api() -> AsyncIterator[ProfileApi]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        for table in schema_tables():
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    identity = IdentityService()
    async with factory.begin() as session:
        await identity.create_user(session, username='admin', password=PASSWORD, is_admin=True)
        member = await identity.create_user(session, username='member', password=PASSWORD)
        department = IdentityDepartment(name='研发部')
        post = IdentityPost(code='engineer', name='工程师')
        role = IdentityRole(code='reader', name='审阅员', permissions=[])
        session.add_all([department, post, role])
        await session.flush()
        user = await session.get(IdentityUser, member.id)
        assert user is not None
        user.department_id = department.id
        session.add_all(
            [
                IdentityUserRole(user_id=member.id, role_id=role.id),
                IdentityUserPost(user_id=member.id, post_id=post.id),
            ]
        )
    audit = Audit()
    container = SimpleNamespace(
        identity=identity, operations=audit, settings=SimpleNamespace(cookie_secure=False, session_ttl_seconds=86400)
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_container] = lambda: container

    async def principal_override(request: Request) -> Principal:
        async with factory() as session:
            return await identity.authenticate(session, request.cookies.get('zhiheng_session'))

    async def session_override() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_principal] = principal_override
    app.dependency_overrides[get_session] = session_override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield ProfileApi(client, audit, factory, member)
    await engine.dispose()


async def login(client: httpx.AsyncClient, username: str = 'member', password: str = PASSWORD) -> str:
    response = await client.post('/api/auth/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.text
    token = client.cookies.get('zhiheng_session')
    assert token is not None
    return token


async def test_profile_returns_only_current_user_and_association_names(api: ProfileApi) -> None:
    assert (await api.client.get('/api/auth/profile')).status_code == 401
    await login(api.client)
    response = await api.client.get('/api/auth/profile')
    assert response.status_code == 200, response.text
    profile = response.json()
    assert set(profile) == {
        'id',
        'username',
        'display_name',
        'email',
        'phone',
        'is_admin',
        'department_name',
        'roles',
        'posts',
        'created_at',
        'version',
    }
    assert profile['id'] == str(api.member.id)
    assert profile['username'] == 'member' and profile['is_admin'] is False
    assert profile['department_name'] == '研发部'
    assert profile['roles'] == ['审阅员'] and profile['posts'] == ['工程师']
    assert profile['created_at'] is not None and profile['version'] == 1
    await login(api.client, 'admin')
    admin = (await api.client.get('/api/auth/profile')).json()
    assert admin['department_name'] is None and admin['posts'] == []
    assert admin['roles'] == ['超级管理员']


async def test_profile_patch_uses_cas_and_preserves_other_users_and_login_contract(api: ProfileApi) -> None:
    await login(api.client)
    response = await api.client.patch(
        '/api/auth/profile',
        json={
            'display_name': ' 本人名称 ',
            'email': 'private@example.test',
            'phone': '13800000000',
            'expected_version': 1,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()['display_name'] == '本人名称' and response.json()['version'] == 2
    assert response.json()['email'] == 'private@example.test'
    assert response.json()['phone'] == '13800000000'
    stale = await api.client.patch('/api/auth/profile', json={'display_name': 'stale', 'expected_version': 1})
    assert stale.status_code == 409
    assert (await api.client.get('/api/auth/profile')).json()['display_name'] == '本人名称'
    me = (await api.client.get('/api/auth/me')).json()
    assert set(me) == {'id', 'username', 'is_admin', 'permissions'}
    await login(api.client, 'admin')
    assert (await api.client.get('/api/auth/profile')).json()['display_name'] == ''
    assert 'private@example.test' not in str(api.audit.calls)
    assert '13800000000' not in str(api.audit.calls)
    assert '本人名称' not in str(api.audit.calls)


@pytest.mark.parametrize(
    'fields',
    [
        {'username': 'changed'},
        {'is_admin': True},
        {'role_ids': []},
        {'post_ids': []},
        {'department_id': None},
        {'id': 'another-user'},
        {'email': None},
        {'phone': None},
        {'display_name': None},
        {'expected_version': True},
        {'expected_version': 0},
    ],
)
async def test_profile_patch_rejects_disallowed_fields(api: ProfileApi, fields: dict[str, object]) -> None:
    await login(api.client)
    response = await api.client.patch('/api/auth/profile', json={'expected_version': 1, **fields})
    assert response.status_code == 422, response.text
    assert (await api.client.get('/api/auth/profile')).json()['version'] == 1


async def test_profile_patch_requires_version(api: ProfileApi) -> None:
    await login(api.client)
    response = await api.client.patch('/api/auth/profile', json={'display_name': 'new'})
    assert response.status_code == 422


async def test_password_validation_failures_keep_all_sessions(api: ProfileApi) -> None:
    first = await login(api.client)
    await login(api.client)
    wrong = await api.client.post(
        '/api/auth/password',
        json={
            'current_password': 'private-wrong-password',
            'new_password': NEW_PASSWORD,
            'expected_version': 1,
        },
    )
    assert wrong.status_code == 400, wrong.text
    assert 'private-wrong-password' not in wrong.text and NEW_PASSWORD not in wrong.text
    assert (await api.client.get('/api/auth/me')).status_code == 200
    unchanged = await api.client.post(
        '/api/auth/password',
        json={
            'current_password': PASSWORD,
            'new_password': PASSWORD,
            'expected_version': 1,
        },
    )
    assert unchanged.status_code == 400
    stale = await api.client.post(
        '/api/auth/password',
        json={
            'current_password': PASSWORD,
            'new_password': NEW_PASSWORD,
            'expected_version': 2,
        },
    )
    assert stale.status_code == 409
    invalid = await api.client.post(
        '/api/auth/password',
        json={
            'current_password': PASSWORD,
            'new_password': 'short',
            'expected_version': 1,
        },
    )
    assert invalid.status_code == 422 and 'short' not in invalid.text
    extra = await api.client.post(
        '/api/auth/password',
        json={
            'current_password': PASSWORD,
            'new_password': NEW_PASSWORD,
            'expected_version': 1,
            'username': 'admin',
        },
    )
    assert extra.status_code == 422
    api.client.cookies.clear()
    api.client.cookies.set('zhiheng_session', first)
    assert (await api.client.get('/api/auth/me')).status_code == 200
    assert (await api.client.get('/api/auth/profile')).json()['version'] == 1


async def test_password_change_revokes_all_sessions_clears_cookie_and_increments_version(api: ProfileApi) -> None:
    first = await login(api.client)
    second = await login(api.client)
    response = await api.client.post(
        '/api/auth/password',
        json={
            'current_password': PASSWORD,
            'new_password': NEW_PASSWORD,
            'expected_version': 1,
        },
    )
    assert response.status_code == 204, response.text
    assert response.content == b''
    assert 'Max-Age=0' in response.headers['set-cookie'] and 'Path=/api' in response.headers['set-cookie']
    assert api.client.cookies.get('zhiheng_session') is None
    for token in (first, second):
        api.client.cookies.clear()
        api.client.cookies.set('zhiheng_session', token)
        assert (await api.client.get('/api/auth/me')).status_code == 401
    api.client.cookies.clear()
    old_login = await api.client.post('/api/auth/login', json={'username': 'member', 'password': PASSWORD})
    assert old_login.status_code == 401
    await login(api.client, password=NEW_PASSWORD)
    assert (await api.client.get('/api/auth/profile')).json()['version'] == 2
    async with api.factory() as session:
        sessions = list(await session.scalars(select(IdentitySession).where(IdentitySession.user_id == api.member.id)))
        assert len(sessions) == 1
    assert PASSWORD not in str(api.audit.calls) and NEW_PASSWORD not in str(api.audit.calls)
    assert [call['action'] for call in api.audit.calls].count('profile.change_password') == 1


@pytest.mark.parametrize(
    'path, body',
    [
        ('/api/auth/profile', {'display_name': 'must-roll-back', 'expected_version': 1}),
        ('/api/auth/password', {'current_password': PASSWORD, 'new_password': NEW_PASSWORD, 'expected_version': 1}),
    ],
)
async def test_audit_failure_rolls_back_changes_and_session_revocation(
    api: ProfileApi,
    path: str,
    body: dict[str, object],
) -> None:
    await login(api.client)
    api.audit.fail = True
    response = await api.client.request('PATCH' if path.endswith('profile') else 'POST', path, json=body)
    assert response.status_code == 503
    assert (await api.client.get('/api/auth/me')).status_code == 200
    profile = (await api.client.get('/api/auth/profile')).json()
    assert profile['display_name'] == '' and profile['version'] == 1
