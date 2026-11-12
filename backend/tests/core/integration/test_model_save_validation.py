"""Saving verifies frozen provider metadata before writes; configured output budgets have no product cap."""

import asyncio
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.provider import ProviderServer
from zhiheng.api.inference.schemas import ChatBody
from zhiheng.modules.agents.public import AgentConfig
from zhiheng.modules.chat.public import ChatGenerationSettings
from zhiheng.modules.models.orm import ModelDefinition


async def configuration(admin: httpx.AsyncClient, provider: ProviderServer) -> dict[str, object]:
    response = await admin.post(
        '/api/models/connections',
        json={
            'name': 'Verified provider',
            'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
            'api_key': 'fixture-only-key',
        },
    )
    assert response.status_code == 201, response.text
    return {
        'name': 'Verified model',
        'connection_id': response.json()['id'],
        'upstream_model': 'provider/model-with/slashes',
        'max_output_tokens': 262144,
    }


async def test_catalog_validation_precedes_save_and_supports_large_output_values(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    body = await configuration(admin, provider)
    provider.catalog_models = [str(body['upstream_model'])]
    response = await admin.post('/api/models', json={**body, 'max_output_tokens': 3000000000})
    assert response.status_code == 201, response.text
    assert response.json()['max_output_tokens'] == 3000000000
    assert provider.catalog_requests == [{'path': '/v1/models', 'authorization': 'Bearer fixture-only-key'}]
    assert provider.requests == []
    model_id = UUID(response.json()['id'])
    async with app.state.container.database.sessions() as session:
        binding = await app.state.container.models.resolve(session, model_id)
        assert binding.max_output_tokens == 3000000000
    assert ChatGenerationSettings(max_output_tokens=262144).max_output_tokens == 262144
    assert (
        ChatBody(model=model_id, messages=[{'role': 'user', 'content': 'hello'}], max_tokens=262144).max_tokens
        == 262144
    )
    assert AgentConfig(name='Large output agent', model_id=model_id, max_tokens=262144).max_tokens == 262144


@pytest.mark.parametrize('status', [401, 403, 429, 500])
async def test_failed_provider_validation_never_creates_a_model(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, status: int
) -> None:
    body = await configuration(admin, provider)
    provider.catalog_status = status
    response = await admin.post('/api/models', json=body)
    assert response.status_code in {422, 429, 502}, response.text
    assert 'private-provider-details' not in response.text and 'fixture-only-key' not in response.text
    async with app.state.container.database.sessions() as session:
        assert list(await session.scalars(select(ModelDefinition))) == []


async def test_unknown_model_is_rejected_and_failed_edit_preserves_original(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    body = await configuration(admin, provider)
    provider.catalog_models = []
    response = await admin.post('/api/models', json=body)
    assert response.status_code == 422 and response.json()['error']['code'] == 'model_validation_not_found'
    provider.catalog_models = [str(body['upstream_model'])]
    saved = await admin.post('/api/models', json=body)
    assert saved.status_code == 201, saved.text
    model_id = saved.json()['id']
    response = await admin.patch('/api/models/' + model_id, json={'upstream_model': 'wrong-model'})
    assert response.status_code == 422, response.text
    current = next(
        row for row in (await admin.get('/api/models?include_disabled=true')).json() if row['id'] == model_id
    )
    assert current == saved.json()


async def test_rotation_during_provider_check_does_not_hold_sql_locks_or_save_stale_credentials(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    body = await configuration(admin, provider)
    provider.catalog_models = [str(body['upstream_model'])]
    provider.catalog_wait = True
    saving = asyncio.create_task(admin.post('/api/models', json=body))
    try:
        assert await asyncio.to_thread(provider.catalog_started.wait, 2)
        rotated = await asyncio.wait_for(
            admin.patch(
                '/api/models/connections/' + str(body['connection_id']), json={'api_key': 'rotated-fixture-key'}
            ),
            timeout=2,
        )
        assert rotated.status_code == 200, rotated.text
    finally:
        provider.catalog_release.set()
    response = await saving
    assert response.status_code == 409 and response.json()['error']['code'] == 'model_configuration_changed'
    async with app.state.container.database.sessions() as session:
        assert list(await session.scalars(select(ModelDefinition))) == []


async def test_disabling_remains_possible_when_provider_is_unavailable(
    admin: httpx.AsyncClient, provider: ProviderServer
) -> None:
    body = await configuration(admin, provider)
    saved = await admin.post('/api/models', json=body)
    assert saved.status_code == 201, saved.text
    path = '/api/models/' + saved.json()['id']
    provider.catalog_status = 500
    assert (await admin.post(path + '/disable', json={})).status_code == 200
    assert (await admin.post(path + '/enable', json={})).status_code == 502
    current = next(
        row for row in (await admin.get('/api/models?include_disabled=true')).json() if row['id'] == saved.json()['id']
    )
    assert not current['enabled']


@pytest.mark.parametrize('value', [0, -1, 1.5, True, 9007199254740992])
async def test_invalid_output_values_are_rejected_before_provider_contact(
    admin: httpx.AsyncClient, provider: ProviderServer, value: object
) -> None:
    body = await configuration(admin, provider)
    response = await admin.post('/api/models', json={**body, 'max_output_tokens': value})
    assert response.status_code == 422
    assert provider.catalog_requests == []
