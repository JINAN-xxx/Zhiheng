"""Disabled billing permits authorized real HTTP/worker calls without usage budgets."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_billing_runtime import issue
from tests.core.integration.test_chat_runs import admit
from tests.core.integration.test_public_protocol import drive
from zhiheng.modules.billing.orm import CreditAccount, CreditHold, CreditLedger, Invocation, UsageAccount
from zhiheng.worker.execution import execute_run


async def exhausted_accounts(admin: httpx.AsyncClient, app: FastAPI) -> UUID:
    user = UUID((await admin.get('/api/auth/me')).json()['id'])
    async with app.state.container.database.sessions.begin() as session:
        session.add(UsageAccount(user_id=user, limit_tokens=1, used_tokens=2_147_483_648, reserved_tokens=0))
        session.add(
            CreditAccount(
                user_id=user,
                enabled=False,
                allocated_points=0,
                reclaimed_points=0,
                used_points=0,
                reserved_points=0,
                version=1,
            )
        )
    return user


@pytest.mark.parametrize('entry', ['chat', 'trial'])
async def test_console_calls_work_without_price_or_balance_and_beyond_the_old_token_limit(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, entry: str
) -> None:
    user = await exhausted_accounts(admin, app)
    if entry == 'chat':
        _, run = await admit(admin, model_id)
    else:
        response = await admin.post(
            f'/api/models/{model_id}/trial', json={'prompt': 'Hello', 'client_request_id': str(uuid4())}
        )
        assert response.status_code == 202, response.text
        run = response.json()
    run_id = UUID(run['id'])
    async with app.state.container.database.sessions() as session:
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run_id))
        assert invocation.reserved_tokens == 0 and not invocation.points_enforced
        assert invocation.rate_snapshot is invocation.reserved_cost is None
        assert await session.get(CreditHold, run_id) is None
    await execute_run(app.state.container, run_id)
    result = (await admin.get(f'/api/runs/{run_id}')).json()
    assert result['status'] == 'completed', result
    assert result['usage'] == {'input_tokens': 8, 'output_tokens': 2, 'status': 'known'}
    async with app.state.container.database.sessions() as session:
        wallet = await session.get(CreditAccount, user)
        tokens = await session.get(UsageAccount, user)
        assert not wallet.enabled and wallet.used_points == wallet.reserved_points == 0
        assert tokens.used_tokens == 2_147_483_658 and tokens.reserved_tokens == 0
        assert await session.scalar(select(func.count()).select_from(CreditLedger)) == 0


@pytest.mark.parametrize('kind', ['chat', 'embedding', 'rerank'])
async def test_public_api_ignores_token_points_and_currency_budgets_but_preserves_rpm(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, kind: str
) -> None:
    user = await exhausted_accounts(admin, app)
    selected_model = model_id
    if kind != 'chat':
        connection = (await admin.get('/api/models')).json()[0]['connection_id']
        created = await admin.post(
            '/api/models', json={'name': kind, 'connection_id': connection, 'upstream_model': kind, 'model_type': kind}
        )
        assert created.status_code == 201, created.text
        selected_model = created.json()['id']
    price_body = (
        {'billing_mode': 'search_unit', 'search_price': '9', 'currency': 'USD'}
        if kind == 'rerank'
        else {'billing_mode': 'token', 'input_price': '9', 'output_price': '9', 'currency': 'USD'}
    )
    price = await admin.post(f'/api/models/{selected_model}/rate-cards', json=price_body)
    assert price.status_code == 201, price.text
    key = await issue(
        admin,
        selected_model,
        monthly_budget='0',
        monthly_points_limit='0',
        tpm_limit=1,
        currency='CNY',
        rpm_limit=1,
        concurrency_limit=1,
    )
    endpoint = {'chat': 'chat/completions', 'embedding': 'embeddings', 'rerank': 'rerank'}[kind]
    payload = (
        {'messages': [{'role': 'user', 'content': 'Hello'}]}
        if kind == 'chat'
        else {'input': ['Hello']}
        if kind == 'embedding'
        else {'query': 'Hello', 'documents': ['Hello document']}
    )
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    task = asyncio.create_task(
        admin.post('/v1/' + endpoint, headers=headers, json={'model': selected_model, **payload})
    )
    response = await drive(app.state.container, task)
    assert response.status_code == 200, response.text
    usage_response = await admin.get(f'/api/developer/applications/{key["application_id"]}/usage')
    assert usage_response.status_code == 200, usage_response.text
    assert usage_response.json()['items'][0]['points_enforced'] is False
    assert usage_response.json()['items'][0]['currency'] is None
    async with app.state.container.database.sessions() as session:
        invocation = await session.scalar(select(Invocation).where(Invocation.key_id == UUID(key['id'])))
        assert invocation.status == 'known' and not invocation.points_enforced
        assert invocation.reserved_tokens == 0
        assert invocation.rate_snapshot is invocation.reserved_cost is invocation.billable_cost is None
        assert invocation.currency is None
        assert await session.get(CreditHold, invocation.run_id) is None
        wallet = await session.get(CreditAccount, user)
        assert wallet.used_points == wallet.reserved_points == 0
    limited = await admin.post('/v1/' + endpoint, headers=headers, json={'model': selected_model, **payload})
    assert limited.status_code == 429 and limited.json()['error']['code'] == 'rate_limit_exceeded'


async def test_unknown_provider_usage_does_not_hold_balance_or_block_the_next_call(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    user = await exhausted_accounts(admin, app)
    changed = await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'no-usage'})
    assert changed.status_code == 200, changed.text
    key = await issue(admin, model_id, monthly_budget='0', monthly_points_limit='0', tpm_limit=1, concurrency_limit=1)
    for _ in range(2):
        task = asyncio.create_task(
            admin.post(
                '/v1/chat/completions',
                headers={'Authorization': 'Bearer ' + key['api_key']},
                json={'model': model_id, 'messages': [{'role': 'user', 'content': 'Hello'}]},
            )
        )
        response = await drive(app.state.container, task)
        assert response.status_code == 200, response.text
    async with app.state.container.database.sessions() as session:
        rows = list(await session.scalars(select(Invocation).where(Invocation.key_id == UUID(key['id']))))
        assert len(rows) == 2
        assert all(row.status == 'unknown' and row.reserved_tokens == 0 for row in rows)
        assert all(row.input_tokens is row.output_tokens is row.billable_cost is None for row in rows)
        assert await session.scalar(select(func.count()).select_from(CreditHold)) == 0
        tokens = await session.get(UsageAccount, user)
        assert tokens.used_tokens == 2_147_483_648 and tokens.reserved_tokens == 0
