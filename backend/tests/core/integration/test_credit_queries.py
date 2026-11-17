"""Read interfaces must work in a database-enforced read-only transaction."""

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, text

from tests.core.integration.test_billing_runtime import prepare
from zhiheng.modules.billing.orm import CreditAccount, CreditControl, CreditHold, CreditLedger
from zhiheng.modules.billing.public import CreditAdjustment
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


async def test_all_queries_work_read_only_and_missing_accounts_stay_uncreated(app: FastAPI) -> None:
    container = app.state.container
    user_id, missing_id = uuid4(), uuid4()
    actor = Principal(user_id, 'reader', False)
    async with container.database.sessions.begin() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        models = (CreditAccount, CreditControl, CreditHold, CreditLedger)
        before = [await session.scalar(select(func.count()).select_from(model)) for model in models]
        queries = container.credits.queries
        state = await queries.state(session)
        assert not state.enabled and state.version == 0
        summary = await queries.summary(session, user_id)
        assert summary.enabled and summary.version == 0 and summary.available_points == 0
        summaries = await queries.summaries(session, [missing_id, user_id])
        assert [row.user_id for row in summaries] == [missing_id, user_id]
        assert all(row.version == 0 and row.available_points == 0 for row in summaries)
        assert (await queries.ledger(session, actor))['items'] == []
        for status in [None, 'reserved', 'unknown', 'settled']:
            assert (await queries.reservations(session, actor, status=status))['total'] == 0
        after = [await session.scalar(select(func.count()).select_from(model)) for model in models]
        assert after == before
        assert after[0] == 0


async def test_query_permissions_and_invalid_filters_fail_without_writes(app: FastAPI) -> None:
    container = app.state.container
    actor = Principal(uuid4(), 'reader', False)
    async with container.database.sessions.begin() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        queries = container.credits.queries
        for method in [queries.ledger, queries.reservations]:
            with pytest.raises(AppError) as denied:
                await method(session, actor, user_id=uuid4())
            assert denied.value.status_code == 403
        for values in [{'page': True}, {'page': 0}, {'page_size': 201}]:
            with pytest.raises(AppError) as invalid:
                await queries.ledger(session, actor, **values)
            assert invalid.value.code == 'invalid_pagination'
        with pytest.raises(AppError) as invalid:
            await queries.reservations(session, actor, status='missing')
        assert invalid.value.code == 'invalid_status'
        assert await session.scalar(select(func.count()).select_from(CreditAccount)) == 0


async def test_query_snapshot_matches_write_result_and_preserves_exact_decimal_json(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    async with container.database.sessions.begin() as session:
        result = await container.credits.adjust(
            session,
            actor,
            actor.id,
            CreditAdjustment(
                kind='grant', points='0.000000000000000001', reason='Exact projection', client_request_id=uuid4().hex
            ),
        )
    async with container.database.sessions.begin() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        summary = await container.credits.queries.summary(session, actor.id)
        assert summary == result and summary.available_points == Decimal('100.000000000000000001')
        assert summary.model_dump(mode='json')['available_points'] == '100.000000000000000001'
        entries = await container.credits.queries.ledger(session, actor)
        assert entries['total'] == 2
        assert any(row['amount'] == '0.000000000000000001' for row in entries['items'])
    response = await admin.get('/api/billing/me')
    assert response.status_code == 200 and response.json() == result.model_dump(mode='json')
