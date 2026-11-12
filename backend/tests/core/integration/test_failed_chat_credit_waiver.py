"""Failed empty private chats release credits without inventing provider usage."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import func, select

from tests.core.integration.test_billing_runtime import issue, prepare
from tests.core.integration.test_chat_runs import admit
from zhiheng.application.execution.executor import InferenceExecutor
from zhiheng.modules.billing.orm import CreditHold, CreditLedger, Invocation, UsageAccount
from zhiheng.modules.chat.orm import Conversation
from zhiheng.modules.models.public import ModelBinding, ModelDelta, ModelMessage, ModelProvider, ProviderError
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx
    from fastapi import FastAPI

    from zhiheng.bootstrap.container import Container


class FailureProvider:
    def __init__(self, delta: ModelDelta | None = None) -> None:
        self.delta = delta

    async def stream(self, _messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
        if self.delta is not None:
            yield self.delta
        raise ProviderError(reason='transport_error')


def executor(container: Container, delta: ModelDelta | None = None) -> InferenceExecutor:
    async def factory(_binding: ModelBinding) -> ModelProvider:
        return FailureProvider(delta)

    return InferenceExecutor(
        sessions=container.database.sessions,
        runs=container.runs,
        usage=container.usage,
        chat=container.chat,
        provider_factory=factory,
        lease_seconds=3,
        timeout_seconds=2,
        lifecycle=container.run_lifecycle,
    )


async def legacy_failure(admin: httpx.AsyncClient, container: Container, model_id: str) -> tuple[UUID, UUID]:
    conversation_id, snapshot = await admit(admin, model_id)
    run_id = UUID(snapshot['id'])
    async with container.database.sessions.begin() as session:
        await container.usage.settle(session, run_id)
        run = await session.get(Run, run_id)
        assert run is not None
        run.status, run.provider_started, run.usage_status = 'failed', True, 'unknown'
    return UUID(conversation_id), run_id


async def test_executor_waives_empty_failed_chat_and_preserves_unknown_provider_facts(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    _, snapshot = await admit(admin, model_id)
    run_id = UUID(snapshot['id'])
    await executor(container).execute(run_id)
    async with container.database.sessions() as session:
        hold = await session.get(CreditHold, run_id)
        meter = await session.scalar(select(Invocation).where(Invocation.run_id == run_id))
        run = await session.get(Run, run_id)
        summary = await container.credits.summary(session, actor.id)
        assert hold.status == 'settled' and hold.charged_points == 0
        assert hold.input_tokens is None and hold.output_tokens is None
        assert meter.status == run.usage_status == 'unknown'
        assert meter.input_tokens is None and meter.output_tokens is None
        assert run.status == 'failed'
        assert summary.reserved_points == summary.used_points == 0
        assert summary.available_points == Decimal('100')
        assert (await session.get(UsageAccount, actor.id)).reserved_tokens == 0
        assert meter.reserved_tokens > 0
        entries = list(await session.scalars(select(CreditLedger).where(CreditLedger.run_id == run_id)))
        waived = [entry for entry in entries if entry.kind == 'failure_waive']
        assert len(waived) == 1
        assert waived[0].available_delta == hold.reserved_points
        assert waived[0].reserved_delta == -hold.reserved_points
    response = await admin.get('/api/billing/me/usage')
    assert response.json()['items'][0]['failure_waived'] is True
    assert response.json()['items'][0]['input_tokens'] is None


@pytest.mark.parametrize('delta', [ModelDelta(text='partial answer'), ModelDelta(reasoning='partial reasoning')])
async def test_partial_output_remains_pending_reconciliation(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, delta: ModelDelta
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    _, snapshot = await admit(admin, model_id)
    run_id = UUID(snapshot['id'])
    await executor(container, delta).execute(run_id)
    assert await container.run_recovery.recover_failed_chat_holds() == 0
    async with container.database.sessions() as session:
        hold = await session.get(CreditHold, run_id)
        assert hold.status == 'unknown' and hold.charged_points is None
        assert (await container.credits.summary(session, actor.id)).reserved_points == hold.reserved_points


async def test_confirmed_usage_is_charged_even_when_chat_fails_without_text(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    _, snapshot = await admit(admin, model_id)
    run_id = UUID(snapshot['id'])
    await executor(container, ModelDelta(input_tokens=1000, output_tokens=200)).execute(run_id)
    async with container.database.sessions() as session:
        hold = await session.get(CreditHold, run_id)
        assert hold.status == 'settled' and hold.charged_points == Decimal('0.2')
        assert (await container.credits.summary(session, actor.id)).used_points == Decimal('0.2')
    assert await container.run_recovery.recover_failed_chat_holds() == 0


async def test_later_meter_confirmation_does_not_release_the_same_estimate_twice(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    _, snapshot = await admit(admin, model_id)
    run_id = UUID(snapshot['id'])
    await executor(container).execute(run_id)
    async with container.database.sessions.begin() as session:
        outcome = await container.metering.reconcile(session, run_id, input_tokens=1000, output_tokens=200)
        assert outcome.status == 'known'
    async with container.database.sessions() as session:
        account = await session.get(UsageAccount, actor.id)
        assert account.reserved_tokens == 0 and account.used_tokens == 1200
        summary = await container.credits.summary(session, actor.id)
        assert summary.available_points == 100 and summary.used_points == 0


async def test_failed_waiver_and_confirmed_settlement_share_one_account_without_deadlock(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    _, first = await admit(admin, model_id)
    _, second = await admit(admin, model_id)
    first_id, second_id = UUID(first['id']), UUID(second['id'])
    async with container.database.sessions.begin() as session:
        run = await session.get(Run, first_id)
        run.status, run.provider_started, run.usage_status = 'failed', True, 'unknown'
    ready = asyncio.Event()

    async def failed() -> None:
        async with container.database.sessions.begin() as session:
            await container.usage.settle(session, first_id)
            ready.set()
            await asyncio.sleep(0.05)
            assert await container.usage.waive_failed_chat(session, first_id)

    async def confirmed() -> None:
        await ready.wait()
        async with container.database.sessions.begin() as session:
            await container.usage.settle(session, second_id, input_tokens=1000, output_tokens=200)

    await asyncio.wait_for(asyncio.gather(failed(), confirmed()), timeout=5)
    async with container.database.sessions() as session:
        summary = await container.credits.summary(session, actor.id)
        assert summary.reserved_points == 0 and summary.used_points == Decimal('0.2')
        assert summary.available_points == Decimal('99.8')
        assert (await session.get(UsageAccount, actor.id)).reserved_tokens == 0


async def test_historical_deleted_chat_waiver_is_concurrent_and_idempotent(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    conversation_id, run_id = await legacy_failure(admin, container, model_id)
    async with container.database.sessions.begin() as session:
        conversation = await session.get(Conversation, conversation_id)
        conversation.deleted_at = utcnow()

    async def waive() -> bool:
        async with container.database.sessions.begin() as session:
            return await container.usage.waive_failed_chat(session, run_id)

    results = await asyncio.wait_for(asyncio.gather(waive(), waive()), timeout=5)
    assert sorted(results) == [False, True]
    assert await container.run_recovery.recover_failed_chat_holds() == 0
    async with container.database.sessions() as session:
        summary = await container.credits.summary(session, actor.id)
        assert summary.available_points == 100 and summary.reserved_points == summary.used_points == 0
        count = await session.scalar(
            select(func.count())
            .select_from(CreditLedger)
            .where(CreditLedger.run_id == run_id, CreditLedger.kind == 'failure_waive')
        )
        assert count == 1


@pytest.mark.parametrize(
    'case', ['running', 'completed', 'cancelled', 'non_chat', 'known_partial_zero', 'structured', 'api_key']
)
async def test_recovery_leaves_other_reservations_unchanged(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, case: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    _, run_id = await legacy_failure(admin, container, model_id)
    key = await issue(admin, model_id) if case == 'api_key' else None
    async with container.database.sessions.begin() as session:
        run = await session.get(Run, run_id)
        hold = await session.get(CreditHold, run_id)
        meter = await session.scalar(select(Invocation).where(Invocation.run_id == run_id))
        if case in ('running', 'completed', 'cancelled'):
            run.status = case
        elif case == 'non_chat':
            run.resource_type, hold.source, meter.source = 'connection_test', 'connection_test', 'connection_test'
        elif case == 'known_partial_zero':
            run.input_tokens = hold.input_tokens = meter.input_tokens = 0
        elif key is not None:
            hold.key_id = meter.key_id = UUID(key['id'])
        else:
            run.result = {'answer': 'structured response'}
    assert await container.run_recovery.recover_failed_chat_holds() == 0
    async with container.database.sessions() as session:
        hold = await session.get(CreditHold, run_id)
        assert hold.status == 'unknown' and hold.charged_points is None
        assert (await container.credits.summary(session, actor.id)).reserved_points == hold.reserved_points
