"""Traceability is read only and never grants business-content authority."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.core.integration.test_billing_runtime import prepare, reserve
from tests.core.permission_support import account, grants
from zhiheng.application.billing.trace import InvocationTraceService
from zhiheng.modules.billing.orm import CreditHold, CreditLedger, Invocation
from zhiheng.modules.billing.public import BillingTrace, BillingTraceQueries
from zhiheng.modules.chat.context_orm import SummaryTask
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


async def test_trace_is_read_only_exact_and_keeps_historical_missing_runs(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    run_id = await reserve(container, actor, model_id)
    async with container.database.sessions.begin() as session:
        await container.usage.settle(session, run_id, input_tokens=1000)
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run_id))
        invocation.currency = 'USD'
        invocation.reserved_cost = Decimal('0.123456789012')
        invocation.created_at = utcnow() - timedelta(days=3)
    queries = BillingTraceQueries()
    async with container.database.sessions.begin() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        value = await queries.detail(session, actor, run_id)
        assert value.invocation.input_tokens == 1000 and value.invocation.output_tokens is None
        assert value.reservation.status == 'unknown' and value.reservation.charged_points is None
        assert value.model_dump(mode='json')['invocation']['reserved_cost'] == '0.123456789012'
        assert value.ledger_total >= 1
        with pytest.raises(AppError) as invalid:
            await queries.detail(session, actor, run_id, ledger_page=0)
        assert invalid.value.status_code == 422
    result = await admin.get(f'/api/billing/usage/{run_id}')
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['execution'] is None and data['related'] == []
    assert data['billing']['invocation']['billable_cost'] is None
    assert 'unknown_since' not in data['billing']['invocation']
    assert (await admin.get(f'/api/billing/usage/{run_id}?ledger_page=0')).status_code == 422
    assert (await admin.get(f'/api/billing/usage/{run_id}?ledger_page=2')).json()['billing']['ledger'] == []
    async with container.database.sessions() as session:
        hold = await session.get(CreditHold, run_id)
        assert hold.status == 'unknown' and hold.charged_points is None
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run_id))
        assert invocation.version == data['billing']['invocation']['version']


async def test_trace_separates_work_initiator_billed_owner_and_finance_readers(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    async with account(admin, app, *grants('platform:usage')) as (initiator, _, member):
        run_id = await reserve(container, actor, model_id)
        async with container.database.sessions.begin() as session:
            session.add(
                Run(
                    id=run_id,
                    owner_id=UUID(member['id']),
                    billing_owner_id=actor.id,
                    resource_type='work_turn',
                    resource_id=uuid4(),
                    client_request_id=uuid4(),
                    fingerprint='trace',
                    payload={'secret': 'private-input'},
                    text='private-output',
                    reasoning='private-reasoning',
                    result={'private': 'result'},
                    error_message='private-error',
                )
            )
        assert (await initiator.get(f'/api/billing/usage/{run_id}')).status_code == 404
        async with account(admin, app, *grants('ai:credits')) as (finance, role, user):
            statements = []

            def capture(
                connection: object,
                cursor: object,
                statement: str,
                parameters: object,
                context: object,
                executemany: bool,
            ) -> None:
                statements.append(statement)

            event.listen(container.database.engine.sync_engine, 'before_cursor_execute', capture)
            try:
                response = await finance.get(f'/api/billing/usage/{run_id}')
            finally:
                event.remove(container.database.engine.sync_engine, 'before_cursor_execute', capture)
            assert response.status_code == 200, response.text
            data = response.json()
            assert data['execution']['owner_id'] == member['id']
            assert data['execution']['billing_owner_id'] == str(actor.id)
            for private in [
                'private-input',
                'private-output',
                'private-reasoning',
                'private-error',
                'payload',
                'result',
            ]:
                assert private not in response.text
            runtime_queries = [sql for sql in statements if 'FROM runtime_runs' in sql]
            assert runtime_queries and all(
                'runtime_runs.text' not in sql and 'runtime_runs.payload' not in sql for sql in runtime_queries
            )
            assert (await finance.get(f'/api/runs/{run_id}')).status_code == 404
            denied = await finance.post(
                f'/api/billing/usage/{run_id}/reconcile',
                json={
                    'reason': 'Read only',
                    'expected_version': 1,
                    'client_request_id': uuid4().hex,
                    'input_tokens': 1,
                    'output_tokens': 1,
                },
            )
            assert denied.status_code == 403
            changed = await admin.patch(
                f'/api/admin/roles/{role["id"]}',
                json={
                    'expected_version': role['version'],
                    'permissions': sorted(grants('platform:usage')),
                },
            )
            assert changed.status_code == 200, changed.text
            assert (await finance.get(f'/api/billing/usage/{run_id}')).status_code == 404
            # Self-billed historical records remain readable without a native Run.
            own = uuid4()
            async with container.database.sessions.begin() as session:
                await container.metering.reserve(
                    session,
                    user_id=UUID(user['id']),
                    run_id=own,
                    model_id=UUID(model_id),
                    source='connection_test',
                    tokens=0,
                )
            assert (await finance.get(f'/api/billing/usage/{own}')).status_code == 200


async def test_summary_links_follow_explicit_parent_only_and_filter_foreign_billing(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    conversation = await admin.post('/api/chat/conversations', json={})
    assert conversation.status_code == 201, conversation.text
    parent_id = await reserve(container, actor, model_id)
    child_id = await reserve(container, actor, model_id)
    unrelated_id = await reserve(container, actor, model_id)
    task_id = uuid4()
    async with container.database.sessions.begin() as session:
        for run_id, kind, resource in [
            (parent_id, 'chat', UUID(conversation.json()['id'])),
            (child_id, 'chat_summary', task_id),
        ]:
            session.add(
                Run(
                    id=run_id,
                    owner_id=actor.id,
                    billing_owner_id=actor.id,
                    resource_type=kind,
                    resource_id=resource,
                    client_request_id=uuid4(),
                    fingerprint='trace',
                    payload={},
                )
            )
        await session.flush()
        session.add(
            SummaryTask(
                id=task_id,
                conversation_id=UUID(conversation.json()['id']),
                owner_id=actor.id,
                parent_run_id=parent_id,
                parent_attempt_id=uuid4(),
                run_id=child_id,
                expected_version=1,
                covered_count=1,
                source_hash='trace',
                source_refs=[],
                file_refs=[],
                model_binding={},
                status='published',
            )
        )
    parent = (await admin.get(f'/api/billing/usage/{parent_id}')).json()
    child = (await admin.get(f'/api/billing/usage/{child_id}')).json()
    assert [(row['run_id'], row['relation']) for row in parent['related']] == [(str(child_id), 'summary')]
    assert [(row['run_id'], row['relation']) for row in child['related']] == [(str(parent_id), 'parent')]
    assert str(unrelated_id) not in str(parent['related'])
    async with container.database.sessions() as session:
        assert (
            await BillingTraceQueries().references(session, Principal(uuid4(), 'other', False), actor.id, [child_id])
            == []
        )
    # Clearing a historical parent keeps the child readable without guessing a relationship.
    async with container.database.sessions.begin() as session:
        task = await session.get(SummaryTask, task_id)
        task.parent_run_id = task.parent_attempt_id = None
    assert (await admin.get(f'/api/billing/usage/{child_id}')).json()['related'] == []


async def test_trace_uses_repeatable_read_readonly_and_pages_a_single_run_ledger(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    run_id = await reserve(container, actor, model_id)
    async with container.database.sessions.begin() as session:
        for _ in range(25):
            session.add(
                CreditLedger(
                    user_id=actor.id,
                    operator_id=actor.id,
                    kind='adjustment',
                    amount=Decimal(0),
                    available_delta=Decimal(0),
                    reserved_delta=Decimal(0),
                    available_after=Decimal(0),
                    reserved_after=Decimal(0),
                    run_id=run_id,
                    reason='Read-only paging fixture',
                )
            )

    class ReadOnlyQueries(BillingTraceQueries):
        async def detail(
            self, session: AsyncSession, actor: Principal, run_id: UUID, *, ledger_page: int = 1
        ) -> BillingTrace:
            assert await session.scalar(text('SHOW transaction_read_only')) == 'on'
            assert await session.scalar(text('SHOW transaction_isolation')) == 'repeatable read'
            return await super().detail(session, actor, run_id, ledger_page=ledger_page)

    service = InvocationTraceService(
        container.database, ReadOnlyQueries(), container.runs, container.chat_context.memory, container.agents
    )
    first = await service.detail(actor, run_id)
    second = await service.detail(actor, run_id, ledger_page=2)
    assert len(first.billing.ledger) == 20 and len(second.billing.ledger) == 6
    assert first.billing.ledger_total == second.billing.ledger_total == 26
    assert not ({row.id for row in first.billing.ledger} & {row.id for row in second.billing.ledger})
    assert all(row.run_id == run_id for row in [*first.billing.ledger, *second.billing.ledger])
