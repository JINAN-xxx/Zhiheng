import httpx
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_empty_database_is_not_initialized_by_api(settings: object) -> None:
    from zhiheng.infrastructure.database import Database
    from zhiheng.shared.errors import AppError

    database = Database(settings.database_url.get_secret_value())
    try:
        with pytest.raises(AppError, match='初始化'):
            await database.require_schema()
        async with database.engine.connect() as connection:
            assert await connection.scalar(text("SELECT to_regclass('identity_users')")) is None
    finally:
        await database.close()


async def test_login_and_logout_revoke_the_session(client: httpx.AsyncClient) -> None:
    result = await client.post('/api/auth/login', json={'username': 'admin', 'password': 'platform-test-password'})
    assert result.status_code == 200
    assert result.json()['user']['is_admin'] is True
    cookie = result.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert (await client.get('/api/auth/me')).json()['username'] == 'admin'
    token = client.cookies.get('zhiheng_session')
    assert (await client.post('/api/auth/logout', json={})).status_code == 204
    client.cookies.set('zhiheng_session', token)
    assert (await client.get('/api/auth/me')).status_code == 401


async def test_login_does_not_echo_secret_input(client: httpx.AsyncClient) -> None:
    result = await client.post('/api/auth/login', json={'username': 'admin', 'password': 'private-password'})
    assert result.status_code == 401
    assert 'private-password' not in result.text
    invalid = await client.post('/api/auth/login', json={'username': ['invalid'], 'password': 'private-password'})
    assert invalid.status_code == 422
    assert 'private-password' not in invalid.text


async def test_cross_origin_login_is_rejected(client: httpx.AsyncClient) -> None:
    result = await client.post(
        '/api/auth/login',
        json={'username': 'admin', 'password': 'platform-test-password'},
        headers={'Origin': 'https://untrusted.example'},
    )
    assert result.status_code == 403


async def test_session_tokens_and_passwords_are_not_stored_in_plaintext(admin: httpx.AsyncClient, app: object) -> None:
    container = app.state.container
    token = admin.cookies.get('zhiheng_session')
    async with container.database.sessions() as session:
        password = await session.scalar(text("SELECT password_hash FROM identity_users WHERE username='admin'"))
        token_hash = await session.scalar(text('SELECT token_hash FROM identity_sessions LIMIT 1'))
    assert password.startswith('$argon2id$')
    assert password != 'platform-test-password'
    assert token_hash != token


async def test_ai_role_changes_refresh_existing_http_sessions(admin: httpx.AsyncClient, app: object) -> None:
    permissions = [
        'ai:models:read',
        'ai:models:write',
        'ai:models:test',
        'ai:rates:read',
        'ai:rates:write',
        'ai:skills:write',
        'ai:storage:read',
        'ai:storage:write',
    ]
    created = await admin.post('/api/admin/roles', json={'name': 'AI operator'})
    assert created.status_code == 201, created.text
    role = created.json()
    assert role['code'].startswith('role_') and len(role['code']) == 37
    created_user = await admin.post(
        '/api/admin/users',
        json={'username': 'ai-operator', 'password': 'platform-test-password', 'role_ids': [role['id']]},
    )
    assert created_user.status_code == 201, created_user.text
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as operator:
        login = await operator.post(
            '/api/auth/login', json={'username': 'ai-operator', 'password': 'platform-test-password'}
        )
        assert login.status_code == 200, login.text
        assert login.json()['user']['permissions'] == []
        changed = await admin.patch(
            f'/api/admin/roles/{role["id"]}', json={'permissions': permissions, 'expected_version': 1}
        )
        assert changed.status_code == 200, changed.text
        current = await operator.get('/api/auth/me')
        assert current.status_code == 200
        assert set(current.json()['permissions']) == set(permissions)
        assert current.json()['is_admin'] is False
        for path in ('/api/admin/permissions', '/api/admin/roles', '/api/admin/menus?navigation=true'):
            assert (await operator.get(path)).status_code == 403
        for version, active in ((2, False), (3, True)):
            changed = await admin.patch(
                f'/api/admin/roles/{role["id"]}', json={'is_active': active, 'expected_version': version}
            )
            assert changed.status_code == 200 and changed.json()['code'] == role['code']
            current = await operator.get('/api/auth/me')
            assert current.status_code == 200
            assert set(current.json()['permissions']) == (set(permissions) if active else set())
        revoked = await admin.patch(f'/api/admin/roles/{role["id"]}', json={'permissions': [], 'expected_version': 4})
        assert revoked.status_code == 200, revoked.text
        assert (await operator.get('/api/auth/me')).json()['permissions'] == []


async def test_full_system_and_ai_permission_catalog_can_be_granted_over_http(admin: httpx.AsyncClient) -> None:
    response = await admin.get('/api/admin/permissions')
    assert response.status_code == 200
    permissions = sorted(item['code'] for item in response.json()['items'])
    assert len(permissions) == 28
    created = await admin.post('/api/admin/roles', json={'name': 'All permissions', 'permissions': permissions})
    assert created.status_code == 201, created.text
    role = created.json()
    assert role['permissions'] == permissions
    updated = await admin.patch(
        f'/api/admin/roles/{role["id"]}', json={'permissions': permissions, 'expected_version': 1}
    )
    assert updated.status_code == 200 and updated.json()['permissions'] == permissions
