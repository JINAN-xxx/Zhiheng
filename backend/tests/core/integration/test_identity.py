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
