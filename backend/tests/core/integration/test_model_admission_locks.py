import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from zhiheng.modules.developer.orm import Invocation, UsageAccount
from zhiheng.shared.errors import AppError


@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('outcome', ['known', 'unknown', 'rejected'])
async def test_binding_validation_allows_concurrent_usage_settlement(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: object, batch: bool, outcome: str
) -> None:
    container = app.state.container
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    model, first, second = UUID(model_id), uuid4(), uuid4()
    response = await admin.post(
        f'/api/models/{model_id}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.02'},
    )
    assert response.status_code == 201, response.text
    async with container.database.sessions.begin() as session:
        rate = await container.models.latest_rate(session, model)
        await container.usage.reserve(
            session,
            user_id=owner,
            run_id=first,
            model_id=model,
            source='connection_test',
            tokens=100,
            token_limit=100000,
            rate=rate,
        )

    async def settle() -> None:
        async with container.database.sessions.begin() as session:
            await container.usage.settle(
                session,
                first,
                input_tokens=10 if outcome == 'known' else None,
                output_tokens=5 if outcome == 'known' else None,
                rejected_before_execution=outcome == 'rejected',
            )

    task = None
    try:
        async with container.database.sessions.begin() as admission:
            if batch:
                await container.models.resolve_many(admission, [model])
            else:
                await container.models.resolve(admission, model)
            # Another request's metadata validation must allow a completed call
            # to settle before this admission reserves the shared account.
            task = asyncio.create_task(settle())
            await asyncio.wait_for(asyncio.shield(task), timeout=2)
            await container.usage.reserve(
                admission,
                user_id=owner,
                run_id=second,
                model_id=model,
                source='connection_test',
                tokens=100,
                token_limit=100000,
                rate=rate,
            )
        async with container.database.sessions.begin() as session:
            invocation = await session.scalar(select(Invocation).where(Invocation.run_id == first))
            account = await session.get(UsageAccount, owner)
            assert invocation.status == ('unknown' if outcome == 'unknown' else 'known')
            assert invocation.reserved_cost == Decimal('2')
            assert invocation.billable_cost == (
                None if outcome == 'unknown' else Decimal('0.2') if outcome == 'known' else Decimal('0')
            )
            assert account.used_tokens == (15 if outcome == 'known' else 0)
            assert account.reserved_tokens == (200 if outcome == 'unknown' else 100)
            again = await container.usage.settle(session, first, input_tokens=99, output_tokens=99)
            assert again.status == invocation.status
        assert provider.requests == []
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('delete', [False, True])
async def test_binding_validation_still_excludes_model_configuration_changes(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, batch: bool, delete: bool
) -> None:
    container = app.state.container
    model = UUID(model_id)
    mutation = None
    try:
        async with container.database.sessions.begin() as admission:
            if batch:
                await container.models.resolve_many(admission, [model])
            else:
                await container.models.resolve(admission, model)
            binding_pid = await admission.scalar(text('SELECT pg_backend_pid()'))
            request = (
                admin.delete(f'/api/models/{model_id}', headers={'Content-Type': 'application/json'})
                if delete
                else admin.patch(f'/api/models/{model_id}', json={'enabled': False})
            )
            mutation = asyncio.create_task(request)
            async with container.database.sessions() as observer:
                for _ in range(100):
                    blocked = await observer.scalar(
                        text('SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid)))'),
                        {'pid': binding_pid},
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
                assert blocked and not mutation.done()
        response = await asyncio.wait_for(mutation, timeout=2)
        assert response.status_code == 200, response.text
        async with container.database.sessions() as session:
            with pytest.raises(AppError, match='模型不存在或不可用'):
                await container.models.resolve(session, model)
    finally:
        if mutation is not None:
            mutation.cancel()
            await asyncio.gather(mutation, return_exceptions=True)
