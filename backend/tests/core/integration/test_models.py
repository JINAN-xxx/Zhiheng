import httpx
import pytest
from sqlalchemy import text

from tests.core.provider import ProviderServer


@pytest.mark.parametrize('replacement', [None, 'original-fixture-key', '   '])
async def test_endpoint_change_requires_a_different_key(admin: httpx.AsyncClient, replacement: str | None) -> None:
    original_url = 'https://original.example/v1'
    created = await admin.post(
        '/api/models/connections',
        json={'name': 'Original', 'base_url': original_url, 'api_key': 'original-fixture-key'},
    )
    assert created.status_code == 201
    new_url = 'https://replacement.example/v1'
    fields = {'base_url': new_url, 'name': 'Rename with address change'}
    if replacement is not None:
        fields['api_key'] = replacement
    path = '/api/models/connections/' + created.json()['id']
    refused = await admin.patch(path, json=fields)
    assert refused.status_code == 422 and 'original-fixture-key' not in refused.text
    before = (await admin.get('/api/models/connections')).json()[0]
    assert before['base_url'] == original_url and before['name'] == 'Original'
    renamed = await admin.patch(path, json={'base_url': original_url + '/', 'name': 'Renamed'})
    assert renamed.status_code == 200 and renamed.json()['base_url'] == original_url
    updated = await admin.patch(path, json={'base_url': new_url, 'api_key': 'replacement-fixture-key'})
    assert updated.status_code == 200 and updated.json()['base_url'] == new_url
    assert 'replacement-fixture-key' not in updated.text


async def test_model_connection_encrypts_secrets(
    admin: httpx.AsyncClient, app: object, provider: ProviderServer
) -> None:
    response = await admin.post(
        '/api/models/connections',
        json={
            'name': 'Local provider',
            'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
            'api_key': 'provider-private-key',
        },
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
    data = response.json()
    assert {name: data[name] for name in ('used_tokens', 'reserved_tokens', 'records')} == {
        'used_tokens': 0,
        'reserved_tokens': 0,
        'records': [],
    }
    assert data['points_enabled'] is False
    assert 'limit_tokens' not in data and 'quota_version' not in data
    assert data['page'] == 1 and data['total'] == 0


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
