"""Enabled models are usable directly; explicit model trials still require permission."""

from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.billing_support import enable_free_billing
from tests.core.integration.test_ai_management_permissions import account
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.provider import ProviderServer
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.modules.models.orm import ModelDefinition
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow
from zhiheng.worker.execution import execute_run


async def legacy_unpublished(app: FastAPI, model_id: str) -> None:
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(ModelDefinition, UUID(model_id))
        assert row is not None
        row.published = False


@pytest.mark.parametrize('admin_actor', [True, False], ids=['admin', 'trial-role'])
async def test_legacy_unpublished_trial_uses_custom_input_existing_ledger_and_idempotency(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, admin_actor: bool
) -> None:
    await enable_free_billing(admin)
    await legacy_unpublished(app, model_id)
    async with account(admin, app, 'ai:models:test') as (tester, _, _):
        actor = admin if admin_actor else tester
        assert model_id in {row['id'] for row in (await actor.get('/api/models')).json()}
        assert model_id in {row['id'] for row in (await actor.get('/api/models?trial=true')).json()}
        if not admin_actor:
            assert (await actor.get('/api/models/connections')).status_code == 403
            assert (await actor.get('/api/models?include_disabled=true')).status_code == 403
        path = '/api/models/' + model_id + '/trial'
        body = {'client_request_id': str(uuid4()), 'prompt': '请用一句话介绍你自己'}
        first = await actor.post(path, json=body)
        assert first.status_code == 202, first.text
        again = await actor.post(path, json=body)
        assert again.json()['id'] == first.json()['id']
        changed = await actor.post(path, json={**body, 'prompt': 'A different trial'})
        assert changed.status_code == 409
        quota = (await actor.get('/api/usage')).json()
        assert quota['reserved_tokens'] > 0 and quota['used_tokens'] == 0
        await execute_run(app.state.container, UUID(first.json()['id']))
        result = (await actor.get('/api/runs/' + first.json()['id'])).json()
        assert result['status'] == 'completed' and result['text'] == '你好', result
        assert provider.requests[0]['messages'] == [{'role': 'user', 'content': body['prompt']}]
        assert len(provider.requests) == 1
        quota = (await actor.get('/api/usage')).json()
        assert quota['reserved_tokens'] == 0 and quota['used_tokens'] > 0 and len(quota['records']) == 1
        row = next(
            row for row in (await admin.get('/api/models?include_disabled=true')).json() if row['id'] == model_id
        )
        assert row['enabled'] and 'published' not in row


@pytest.mark.parametrize('permission', [None, 'ai:models:read', 'ai:models:write'])
async def test_configuration_permissions_do_not_grant_trial_access(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, permission: str | None
) -> None:
    await legacy_unpublished(app, model_id)
    async with account(admin, app, *([permission] if permission else [])) as (actor, _, _):
        assert (await actor.get('/api/models?trial=true')).status_code == 403
        assert (
            await actor.post(
                '/api/models/' + model_id + '/trial', json={'client_request_id': str(uuid4()), 'prompt': 'Denied'}
            )
        ).status_code == 403
        assert (
            await actor.post('/api/models/' + model_id + '/test', json={'client_request_id': str(uuid4())})
        ).status_code == 403
    assert not provider.requests


async def test_disabled_model_is_unavailable_to_chat_knowledge_and_public_api(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['models:invoke'], model_ids=[model_id])
    await legacy_unpublished(app, model_id)
    assert (await admin.post('/api/models/' + model_id + '/disable', json={})).status_code == 200
    for actor in (admin,):
        conversation = (await actor.post('/api/chat/conversations', json={})).json()
        denied = await actor.post(
            '/api/chat/conversations/' + conversation['id'] + '/runs',
            json={'client_request_id': str(uuid4()), 'model_id': model_id, 'message': 'Formal use is forbidden'},
        )
        assert denied.status_code == 404, denied.text
        assert (
            await actor.post('/api/knowledge/bases', json={'name': 'Not allowed', 'embed_model_id': model_id})
        ).status_code == 404
    response = await admin.post(
        '/v1/chat/completions',
        headers={'Authorization': 'Bearer ' + key['api_key']},
        json={'model': model_id, 'messages': [{'role': 'user', 'content': 'Denied'}]},
    )
    assert response.status_code == 404, response.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        denied = await anonymous.post(
            '/api/models/' + model_id + '/trial',
            headers={'Authorization': 'Bearer ' + key['api_key']},
            json={'client_request_id': str(uuid4()), 'prompt': 'No cookie'},
        )
        assert denied.status_code == 401, denied.text
    assert not provider.requests


@pytest.mark.parametrize('change', ['permission', 'account', 'model', 'connection', 'deleted'])
async def test_queued_trial_rechecks_current_authority_before_provider(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, change: str
) -> None:
    await legacy_unpublished(app, model_id)
    container = app.state.container
    async with account(admin, app, 'ai:models:test') as (actor, role, user):
        result = await actor.post(
            '/api/models/' + model_id + '/trial', json={'client_request_id': str(uuid4()), 'prompt': 'Never sent'}
        )
        assert result.status_code == 202, result.text
        if change == 'permission':
            response = await admin.patch(
                '/api/admin/roles/' + role['id'], json={'permissions': [], 'expected_version': role['version']}
            )
            assert response.status_code == 200, response.text
        elif change == 'account':
            async with container.database.sessions.begin() as session:
                (await session.get(IdentityUser, UUID(user['id']))).is_active = False
        elif change == 'model':
            assert (await admin.post('/api/models/' + model_id + '/disable', json={})).status_code == 200
        elif change == 'connection':
            models = (await admin.get('/api/models?include_disabled=true')).json()
            connection_id = next(row['connection_id'] for row in models if row['id'] == model_id)
            assert (
                await admin.post('/api/models/connections/' + connection_id + '/disable', json={})
            ).status_code == 200
        else:
            async with container.database.sessions.begin() as session:
                (await session.get(ModelDefinition, UUID(model_id))).deleted_at = utcnow()
        await execute_run(container, UUID(result.json()['id']))
        async with container.database.sessions() as session:
            row = await session.get(Run, UUID(result.json()['id']))
            assert row.status == 'failed' and not row.provider_started, row.status
            assert row.usage_status == 'known' and row.input_tokens == 0 and row.output_tokens == 0
        assert not provider.requests


@pytest.mark.parametrize(
    'body',
    [
        {'prompt': ''},
        {'prompt': ' ' * 2},
        {'prompt': 'A' * 4001},
        {'prompt': 'test', 'documents': ['unexpected']},
        {'prompt': 'test', 'resource_type': 'chat'},
        {'prompt': 'test', 'documents': ['A' * 1001]},
    ],
)
async def test_invalid_trial_inputs_never_reserve_or_call(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, body: dict
) -> None:
    response = await admin.post('/api/models/' + model_id + '/trial', json={'client_request_id': str(uuid4()), **body})
    assert response.status_code == 422, response.text
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Run)) == 0
    assert not provider.requests


@pytest.mark.parametrize('kind', ['embedding', 'rerank'])
async def test_structured_trials_use_user_text_and_return_results(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, kind: str
) -> None:
    connection = (await admin.get('/api/models/connections')).json()[0]
    model = await admin.post(
        '/api/models',
        json={
            'name': 'Trial ' + kind,
            'connection_id': connection['id'],
            'upstream_model': 'local-' + kind,
            'model_type': kind,
        },
    )
    assert model.status_code == 201, model.text
    body = {'client_request_id': str(uuid4()), 'prompt': '测试文本'}
    if kind == 'rerank':
        body['documents'] = ['测试文本内容', '另一份候选资料']
    result = await admin.post('/api/models/' + model.json()['id'] + '/trial', json=body)
    assert result.status_code == 202, result.text
    await execute_run(app.state.container, UUID(result.json()['id']))
    snapshot = (await admin.get('/api/runs/' + result.json()['id'])).json()
    assert snapshot['status'] == 'completed', snapshot
    assert ('data' if kind == 'embedding' else 'results') in snapshot['result']
    request = provider.requests[-1]
    if kind == 'embedding':
        assert request['input'] == ['测试文本']
    else:
        assert request['query'] == '测试文本' and request['documents'] == body['documents']


async def test_existing_connection_probe_supports_legacy_unpublished_models(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    await legacy_unpublished(app, model_id)
    result = await admin.post('/api/models/' + model_id + '/test', json={'client_request_id': str(uuid4())})
    assert result.status_code == 202, result.text
    await execute_run(app.state.container, UUID(result.json()['id']))
    assert (await admin.get('/api/runs/' + result.json()['id'])).json()['status'] == 'completed'
    assert len(provider.requests) == 1


async def test_enabled_legacy_unpublished_model_is_available_to_chat_and_authorized_api(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    import asyncio

    from tests.core.integration.test_public_protocol import drive

    await legacy_unpublished(app, model_id)
    async with account(admin, app) as (member, _, _):
        catalog = (await member.get('/api/models')).json()
        row = next(row for row in catalog if row['id'] == model_id)
        assert row['enabled'] and 'published' not in row
        conversation = (await member.post('/api/chat/conversations', json={})).json()
        result = await member.post(
            '/api/chat/conversations/' + conversation['id'] + '/runs',
            json={'client_request_id': str(uuid4()), 'model_id': model_id, 'message': 'Enabled is enough'},
        )
        assert result.status_code == 202, result.text
        await execute_run(app.state.container, UUID(result.json()['id']))
        assert (await member.get('/api/runs/' + result.json()['id'])).json()['status'] == 'completed'
    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['models:invoke'], model_ids=[model_id])
    pending = asyncio.create_task(
        admin.post(
            '/v1/chat/completions',
            headers={'Authorization': 'Bearer ' + key['api_key']},
            json={'model': model_id, 'messages': [{'role': 'user', 'content': 'API also works'}]},
        )
    )
    response = await drive(app.state.container, pending)
    assert response.status_code == 200, response.text
    assert len(provider.requests) == 2
    async with app.state.container.database.sessions() as session:
        assert model_id in {str(row.id) for row in await app.state.container.models.billing_models(session)}
        assert await app.state.container.models.is_public(session, UUID(model_id))
        assert UUID(model_id) in await app.state.container.models.resolve_many(session, [UUID(model_id)])


async def test_publication_is_no_longer_a_model_http_contract(admin: httpx.AsyncClient, model_id: str) -> None:
    assert (await admin.patch('/api/models/' + model_id, json={'published': False})).status_code == 422
    for action in ('publish', 'unpublish'):
        assert (await admin.post('/api/models/' + model_id + '/' + action, json={})).status_code == 422
