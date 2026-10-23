import httpx
from sqlalchemy import text


async def test_model_connection_encrypts_secrets(admin: httpx.AsyncClient, app: object) -> None:
    response = await admin.post(
        '/api/models/connections',
        json={'name': 'Local provider', 'base_url': 'http://127.0.0.1:19099/v1', 'api_key': 'provider-private-key'},
    )
    assert response.status_code == 201, response.text
    assert response.json()['has_api_key'] is True
    assert 'provider-private-key' not in response.text
    async with app.state.container.database.sessions() as session:
        ciphertext = await session.scalar(text('SELECT encrypted_api_key FROM model_connections'))
    assert ciphertext != 'provider-private-key'
    assert app.state.container.cipher.decrypt(ciphertext) == 'provider-private-key'
    model = await admin.post(
        '/api/models',
        json={
            'name': 'Text model',
            'connection_id': response.json()['id'],
            'upstream_model': 'local-text',
            'max_output_tokens': 64,
        },
    )
    assert model.status_code == 201, model.text
    assert model.json()['capabilities'] == ['chat', 'stream']


async def test_member_cannot_create_model_connections(client: httpx.AsyncClient) -> None:
    await client.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
    response = await client.post(
        '/api/models/connections', json={'name': 'Forbidden', 'base_url': 'https://example.com/v1', 'api_key': 'secret'}
    )
    assert response.status_code == 403


async def test_invalid_model_key_is_not_echoed(admin: httpx.AsyncClient) -> None:
    response = await admin.post(
        '/api/models/connections', json={'name': 'Invalid', 'base_url': 'javascript:bad', 'api_key': 'private-secret'}
    )
    assert response.status_code == 422
    assert 'private-secret' not in response.text


async def test_new_account_usage_is_empty(admin: httpx.AsyncClient) -> None:
    response = await admin.get('/api/usage')
    assert response.status_code == 200
    assert response.json() == {'limit_tokens': 100000, 'used_tokens': 0, 'reserved_tokens': 0, 'records': []}


async def test_unsupported_provider_is_rejected_instead_of_ignored(admin: httpx.AsyncClient) -> None:
    response = await admin.post(
        '/api/models/connections',
        json={
            'name': 'Unsupported',
            'base_url': 'https://example.com/v1',
            'api_key': 'private-key',
            'provider': 'anthropic',
        },
    )
    assert response.status_code == 422
    assert 'private-key' not in response.text
