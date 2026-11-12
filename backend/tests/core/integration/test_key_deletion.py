"""Soft deletion revokes current authority without erasing original billing facts."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.auth_personal_support import identity_client
from tests.core.integration.test_billing_http import mutation, point_rate
from tests.core.integration.test_developer import admitted, provision
from tests.core.integration.test_resource_files import auth, post
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.integration.test_resource_knowledge import SCOPES, finish, setup
from tests.core.provider import ProviderServer
from zhiheng.modules.billing.orm import CreditHold, CreditLedger, Invocation, UsageAccount
from zhiheng.modules.developer.orm import APIRequest, DeveloperKey
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow
from zhiheng.worker.execution import execute_run


async def remove(client: httpx.AsyncClient, app_id: str, key_id: str) -> httpx.Response:
    return await client.delete(
        f'/api/developer/applications/{app_id}/keys/{key_id}', headers={'Content-Type': 'application/json'}
    )


@pytest.mark.parametrize('state', ['active', 'retiring', 'revoked', 'expired'])
async def test_delete_any_key_state_hides_it_and_preserves_identity(
    admin: httpx.AsyncClient, app: FastAPI, state: str
) -> None:
    owned = await application(admin)
    key = await issue(admin, owned['id'], scopes=['files:read'])
    survivor = await issue(admin, owned['id'], scopes=['files:read'])
    path = f'/api/developer/applications/{owned["id"]}/keys/{key["id"]}'
    if state == 'retiring':
        assert (await admin.post(path + '/rotate', json={'grace_period_seconds': 60})).status_code == 201
    elif state == 'revoked':
        assert (await admin.post(path + '/revoke', json={})).status_code == 200
    elif state == 'expired':
        async with app.state.container.database.sessions.begin() as session:
            row = await session.get(DeveloperKey, UUID(key['id']))
            row.expires_at = utcnow() - timedelta(seconds=1)
    assert (await admin.get(path)).json()['status'] == state
    before = (await admin.get(f'/api/developer/applications/{owned["id"]}/keys')).json()
    assert key['id'] in {item['id'] for item in before['items']}
    response = await remove(admin, owned['id'], key['id'])
    assert response.status_code == 204 and not response.content, response.text
    listed = (await admin.get(f'/api/developer/applications/{owned["id"]}/keys')).json()
    assert listed['total'] == before['total'] - 1
    assert key['id'] not in {item['id'] for item in listed['items']}
    assert survivor['id'] in {item['id'] for item in listed['items']}
    assert (await admin.get(path)).status_code == 404
    assert (await admin.post(path + '/revoke', json={})).status_code == 404
    assert (await admin.post(path + '/rotate', json={})).status_code == 404
    assert (await admin.get('/api/v1/files', headers=auth(key))).status_code == 401
    async with app.state.container.database.sessions() as session:
        row = await session.get(DeveloperKey, UUID(key['id']))
        assert row is not None and row.status == 'revoked' and row.retire_at is None
        assert row.deleted_at is not None and row.name == key['name'] and row.prefix == key['prefix']
        deleted_at = row.deleted_at
    assert (await remove(admin, owned['id'], key['id'])).status_code == 204
    async with app.state.container.database.sessions() as session:
        row = await session.get(DeveloperKey, UUID(key['id']))
        assert row.deleted_at == deleted_at


async def test_key_delete_checks_current_application_authority_and_parent(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    async with identity_client(app) as owner, identity_client(app) as stranger:
        owned, sibling = await application(owner), await application(owner)
        key = await issue(owner, owned['id'], scopes=['files:read'])
        assert (await stranger.get(f'/api/developer/applications/{owned["id"]}')).status_code == 404
        assert (await remove(stranger, owned['id'], key['id'])).status_code == 404
        assert (await remove(owner, sibling['id'], key['id'])).status_code == 404
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
            assert (await remove(anonymous, owned['id'], key['id'])).status_code == 401
            denied = await anonymous.delete(
                f'/api/developer/applications/{owned["id"]}/keys/{key["id"]}',
                headers={**auth(key), 'Content-Type': 'application/json'},
            )
            assert denied.status_code == 401
        forbidden = await owner.delete(
            f'/api/developer/applications/{owned["id"]}/keys/{key["id"]}',
            headers={'Content-Type': 'application/json', 'Origin': 'https://outside.example.invalid'},
        )
        assert forbidden.status_code == 403
        assert (await owner.get('/api/v1/files', headers=auth(key))).status_code == 200
        assert (await remove(owner, owned['id'], key['id'])).status_code == 204
        admin_target = await issue(owner, owned['id'], scopes=['files:read'])
        assert (await remove(admin, owned['id'], admin_target['id'])).status_code == 404
        assert (
            await owner.get(f'/api/developer/applications/{owned["id"]}/keys/{admin_target["id"]}')
        ).status_code == 200
        assert (await remove(stranger, owned['id'], key['id'])).status_code == 404
        assert (await remove(owner, owned['id'], key['id'])).status_code == 204


async def test_delete_contract_uses_console_cookie_and_rejects_authority_fields(admin: httpx.AsyncClient) -> None:
    schema = (await admin.get('/openapi.json')).json()
    path = '/api/developer/applications/{application_id}/keys/{key_id}'
    operation = schema['paths'][path]['delete']
    assert operation['security'] == [{'ConsoleCookie': []}]
    assert '204' in operation['responses']
    owned = await application(admin)
    key = await issue(admin, owned['id'], scopes=['files:read'])
    rejected = await admin.request(
        'DELETE',
        f'/api/developer/applications/{owned["id"]}/keys/{key["id"]}',
        json={'owner_id': 'another-user'},
    )
    assert rejected.status_code == 422
    assert (await admin.get('/api/v1/files', headers=auth(key))).status_code == 200
    assert (await remove(admin, owned['id'], key['id'])).status_code == 204


async def test_completed_usage_remains_readable_after_key_deletion(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    app_id, key = await provision(admin, model_id)
    run, _identity = await admitted(app.state.container, key['api_key'], model_id)
    await execute_run(app.state.container, run.id)
    before = (await admin.get(f'/api/developer/applications/{app_id}/usage')).json()
    assert before['total'] == 1 and before['items'][0]['status'] == 'known'
    assert before['items'][0]['key_id'] == key['id']
    assert len(provider.requests) == 1
    assert (await remove(admin, app_id, key['id'])).status_code == 204
    assert (await admin.get(f'/api/developer/applications/{app_id}/usage')).json() == before
    assert (await admin.get(f'/api/runs/{run.id}')).json()['status'] == 'completed'
    assert (await admin.get('/v1/models', headers=auth(key))).status_code == 401
    assert len(provider.requests) == 1


async def test_deletion_stops_admitted_execution_without_calling_provider(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    app_id, key = await provision(admin, model_id)
    container = app.state.container
    run, identity = await admitted(container, key['api_key'], model_id)
    assert (await remove(admin, app_id, key['id'])).status_code == 204
    await execute_run(container, run.id)
    async with container.database.sessions() as session:
        persisted = await session.get(Run, run.id)
        invoice = await session.scalar(select(Invocation).where(Invocation.run_id == run.id))
        account = await session.get(UsageAccount, identity.owner_id)
        assert persisted.status == 'failed' and invoice.status == 'known'
        assert invoice.key_id == identity.key_id and invoice.charged_tokens == 0
        assert account.reserved_tokens == account.used_tokens == 0
    assert provider.requests == []


async def test_provider_started_before_deletion_can_finish_original_settlement(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    changed = await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'silent'})
    assert changed.status_code == 200, changed.text
    app_id, key = await provision(admin, model_id)
    container = app.state.container
    run, identity = await admitted(container, key['api_key'], model_id)
    execution = asyncio.create_task(execute_run(container, run.id))
    try:
        for _ in range(300):
            if provider.requests:
                break
            await asyncio.sleep(0.01)
        assert len(provider.requests) == 1
        assert (await remove(admin, app_id, key['id'])).status_code == 204
        assert (await admin.get('/v1/models', headers=auth(key))).status_code == 401
    finally:
        provider.release.set()
        await asyncio.wait_for(execution, timeout=5)
    async with container.database.sessions() as session:
        invoice = await session.scalar(select(Invocation).where(Invocation.run_id == run.id))
        account = await session.get(UsageAccount, identity.owner_id)
        completed = await session.get(Run, run.id)
        assert completed.status == 'completed' and invoice.status == 'known'
        assert invoice.key_id == identity.key_id and invoice.charged_tokens > 0
        assert account.reserved_tokens == 0 and account.used_tokens == invoice.charged_tokens
    assert len(provider.requests) == 1


async def test_deletion_keeps_unknown_holds_and_allows_later_reconciliation(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    app_id, key = await provision(admin, model_id)
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    await point_rate(admin, model_id)
    granted = await admin.post(f'/api/billing/accounts/{owner}/adjust', json=mutation(kind='grant', points='10000'))
    assert granted.status_code == 200, granted.text
    assert (await admin.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0))).status_code == 200
    container = app.state.container
    run, identity = await admitted(container, key['api_key'], model_id)
    async with container.database.sessions.begin() as session:
        result = await container.usage.settle(session, run.id)
        assert result.status == 'unknown'
        stored = await session.get(Run, run.id)
        stored.status = 'failed'
        before = await container.credits.summary(session, owner)
        assert before.reserved_points > 0
        ledger_ids = list(await session.scalars(select(CreditLedger.id).order_by(CreditLedger.id)))
    assert (await remove(admin, app_id, key['id'])).status_code == 204
    async with container.database.sessions.begin() as session:
        assert await container.credits.summary(session, owner) == before
        assert list(await session.scalars(select(CreditLedger.id).order_by(CreditLedger.id))) == ledger_ids
        invoice = await session.scalar(select(Invocation).where(Invocation.run_id == run.id))
        hold = await session.get(CreditHold, run.id)
        assert invoice.key_id == hold.key_id == identity.key_id
        assert invoice.status == hold.status == 'unknown'
        assert await session.get(DeveloperKey, identity.key_id) is not None
        version = hold.version
    reconciled = await admin.post(
        f'/api/billing/usage/{run.id}/reconcile',
        json=mutation(expected_version=version, input_tokens=2, output_tokens=3),
    )
    assert reconciled.status_code == 200, reconciled.text
    async with container.database.sessions() as session:
        after = await container.credits.summary(session, owner)
        tokens = await session.get(UsageAccount, owner)
        hold = await session.get(CreditHold, run.id)
        invoice = await session.scalar(select(Invocation).where(Invocation.run_id == run.id))
        assert after.reserved_points == 0 and after.used_points > 0
        assert tokens.reserved_tokens == 0 and tokens.used_tokens == 5
        assert hold.status == 'settled' and invoice.status == 'known'
        assert invoice.billable_cost == Decimal('0.08') and invoice.key_id == identity.key_id
    usage = (await admin.get(f'/api/developer/applications/{app_id}/usage')).json()
    assert usage['total'] == 1 and usage['items'][0]['run_id'] == str(run.id)
    assert provider.requests == []


@pytest.mark.parametrize('at_model', [False, True])
async def test_deleted_resource_key_cannot_continue_original_operation(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, at_model: bool
) -> None:
    owned, key, base = await setup(admin, provider, app)
    observer = await issue(admin, owned['id'], scopes=SCOPES)
    file = await post(admin, key)
    admitted_response = await admin.post(
        f'/api/v1/knowledge-bases/{base["id"]}/documents', headers=auth(key), json={'file_ids': [file.json()['id']]}
    )
    assert admitted_response.status_code == 202, admitted_response.text
    operation = admitted_response.json()
    container = app.state.container
    if at_model:
        for _ in range(3):
            await container.knowledge_jobs.tick(UUID(operation['native_id']))
        async with container.database.sessions() as session:
            child = await session.scalar(select(Run.id).where(Run.status == 'pending'))
        assert child is not None
    assert (await remove(admin, owned['id'], key['id'])).status_code == 204
    if at_model:
        await execute_run(container, child)
    terminal = await finish(admin, app, observer, operation)
    assert terminal['status'] == 'failed' and provider.requests == [], terminal
    async with container.database.sessions() as session:
        origin = await session.get(APIRequest, UUID(operation['id']))
        assert origin is not None and origin.key_id == UUID(key['id'])
        invoices = list(await session.scalars(select(Invocation)))
        assert len(invoices) == int(at_model)
        assert all(row.key_id == UUID(key['id']) and row.status != 'pending' for row in invoices)


async def test_concurrent_deletion_and_rotation_keep_original_key_revoked(admin: httpx.AsyncClient) -> None:
    owned = await application(admin)
    key = await issue(admin, owned['id'], scopes=['files:read'])
    rotation = f'/api/developer/applications/{owned["id"]}/keys/{key["id"]}/rotate'
    deleted, rotated = await asyncio.wait_for(
        asyncio.gather(remove(admin, owned['id'], key['id']), admin.post(rotation, json={})), timeout=5
    )
    assert deleted.status_code == 204, deleted.text
    assert rotated.status_code in {201, 404}, rotated.text
    assert (await admin.get('/api/v1/files', headers=auth(key))).status_code == 401
    items = (await admin.get(f'/api/developer/applications/{owned["id"]}/keys')).json()['items']
    assert key['id'] not in {item['id'] for item in items}
    assert len(items) == int(rotated.status_code == 201)
