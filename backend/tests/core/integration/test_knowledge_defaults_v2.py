"""Knowledge defaults are explicit, versioned and restricted to platform managers."""

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_knowledge import embedding_model
from tests.core.provider import ProviderServer


async def test_explicit_defaults_create_simple_private_library(
    admin: httpx.AsyncClient, provider: ProviderServer
) -> None:
    model = await embedding_model(admin, provider)
    current = await admin.get('/api/knowledge/settings')
    assert current.status_code == 200, current.text
    missing = await admin.post('/api/knowledge/bases', json={'name': 'No implicit model'})
    assert missing.status_code == 409, missing.text
    assert missing.json()['error']['code'] == 'knowledge_defaults_missing'
    saved = await admin.patch(
        '/api/knowledge/settings',
        json={
            'expected_version': current.json()['version'],
            'default_embed_model_id': model,
            'default_rerank_model_id': current.json()['default_rerank_model_id'],
            'chunk_size': 1000,
            'chunk_overlap': 200,
        },
    )
    assert saved.status_code == 200, saved.text
    created = await admin.post('/api/knowledge/bases', json={'name': 'Simple library'})
    assert created.status_code == 201, created.text
    assert created.json()['embed_model_id'] == model
    assert created.json()['access'] == 'private' and created.json()['document_policy'] == 'library'
    capabilities = (await admin.get('/api/knowledge/capabilities')).json()
    assert capabilities['default_embedding_configured'] is True


async def test_default_model_type_and_version_are_validated(
    admin: httpx.AsyncClient, provider: ProviderServer, model_id: str
) -> None:
    model = await embedding_model(admin, provider)
    current = (await admin.get('/api/knowledge/settings')).json()
    version = current['version']
    invalid = await admin.patch(
        '/api/knowledge/settings', json={'expected_version': version, 'default_embed_model_id': model_id}
    )
    assert invalid.status_code == 422, invalid.text
    invalid_ranker = await admin.patch(
        '/api/knowledge/settings', json={'expected_version': version, 'default_rerank_model_id': model}
    )
    assert invalid_ranker.status_code == 422, invalid_ranker.text
    first = await admin.patch(
        '/api/knowledge/settings', json={'expected_version': version, 'default_embed_model_id': model}
    )
    assert first.status_code == 200, first.text
    stale = await admin.patch(
        '/api/knowledge/settings', json={'expected_version': version, 'default_embed_model_id': None}
    )
    assert stale.status_code == 409, stale.text
    assert (await admin.get('/api/knowledge/settings')).json()['default_embed_model_id'] == model


async def test_personal_user_cannot_manage_platform_defaults(admin: httpx.AsyncClient, app: FastAPI) -> None:
    container = app.state.container
    async with container.database.sessions.begin() as session:
        await container.accounts.create_user(
            session, username='defaults-reader', password='strong-local-password', is_admin=False
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as reader:
        assert (
            await reader.post(
                '/api/auth/login', json={'username': 'defaults-reader', 'password': 'strong-local-password'}
            )
        ).status_code == 200
        assert (await reader.get('/api/knowledge/settings')).status_code == 403
        assert (
            await reader.patch('/api/knowledge/settings', json={'expected_version': 1, 'default_rerank_model_id': None})
        ).status_code == 403


async def test_chat_upload_is_temporary_until_actual_message_attachment(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    uploaded = await admin.post(
        '/api/chat/attachments?filename=waiting.txt',
        content=b'Knowledge note',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    info = uploaded.json()
    assert info['purpose'] == 'chat_attachment'
    expiry = datetime.fromisoformat(info['expires_at'].replace('Z', '+00:00'))
    assert 23 * 3600 < (expiry - datetime.now(timezone.utc)).total_seconds() < 25 * 3600
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    sent = await admin.post(
        f'/api/chat/conversations/{conversation["id"]}/runs',
        json={
            'model_id': model_id,
            'message': 'Use my file',
            'file_ids': [info['id']],
            'client_request_id': str(uuid4()),
        },
    )
    assert sent.status_code == 202, sent.text
    assert (await admin.get(f'/api/files/{info["id"]}')).json()['expires_at'] is None
    messages = (await admin.get(f'/api/chat/conversations/{conversation["id"]}/messages')).json()
    assert messages[0]['attachments'][0]['owner_id'] == info['owner_id']


@pytest.mark.parametrize('permission', ['ai:storage:read', 'ai:storage:write'])
async def test_storage_operator_has_only_the_corresponding_admin_entry(
    admin: httpx.AsyncClient, app: FastAPI, permission: str
) -> None:
    role = await admin.post(
        '/api/admin/roles', json={'code': 'storage-only', 'name': 'Storage operator', 'permissions': [permission]}
    )
    assert role.status_code == 201, role.text
    user = await admin.post(
        '/api/admin/users',
        json={'username': 'storage-only', 'password': 'strong-local-password', 'role_ids': [role.json()['id']]},
    )
    assert user.status_code == 201, user.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as reader:
        assert (
            await reader.post('/api/auth/login', json={'username': 'storage-only', 'password': 'strong-local-password'})
        ).status_code == 200
        navigation = await reader.get('/api/admin/menus?navigation=true')
        assert navigation.status_code == 404, navigation.text
        assert (await reader.get('/api/files/policies')).status_code == 200
        assert (await reader.get('/api/knowledge/settings')).status_code == 403
