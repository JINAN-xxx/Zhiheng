"""Recovery must release one invocation's Key/account locks before taking the next Key."""

import asyncio
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.core.integration.test_developer import admitted, provision
from zhiheng.modules.developer.orm import DeveloperKey, Invocation, UsageAccount
from zhiheng.modules.developer.public import UsageOutcome, UsageService
from zhiheng.runtime.contracts import ClaimedRun, RunUsage
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.worker.main import recover_expired


async def recovery_state(
    container: object, owner_id: UUID, recovered_ids: set[UUID], finished_id: UUID, provider_started: bool
) -> dict[str, object]:
    async with container.database.sessions() as session:
        runs = list(await session.scalars(select(Run).order_by(Run.id)))
        invocations = list(await session.scalars(select(Invocation).order_by(Invocation.run_id)))
        account = await session.get(UsageAccount, owner_id)
        expected_reserved = sum(row.reserved_tokens for row in invocations if row.status in {'pending', 'unknown'})
        assert account.reserved_tokens == expected_reserved and account.used_tokens == 10
        finished = next(row for row in invocations if row.run_id == finished_id)
        assert (
            finished.status == 'known' and finished.charged_tokens == 10 and finished.billable_cost == Decimal('0.12')
        )
        assert next(row for row in runs if row.id == finished_id).text == 'normal result'
        for row in invocations:
            if row.run_id in recovered_ids:
                assert row.status == ('unknown' if provider_started else 'known')
                assert row.billable_cost == (None if provider_started else Decimal(0))
                assert row.charged_tokens == (None if provider_started else 0)
        for row in runs:
            if row.id in recovered_ids:
                assert row.status == 'interrupted'
                assert row.usage_status == ('unknown' if provider_started else 'known')
        return {
            'runs': [(row.id, row.status, row.revision) for row in runs],
            'invocations': [(row.run_id, row.status, row.charged_tokens, row.billable_cost) for row in invocations],
            'account': (account.used_tokens, account.reserved_tokens),
        }


async def assert_recovered_attempts_fenced(
    container: object, recovered_ids: set[UUID], claims: dict[UUID, ClaimedRun]
) -> None:
    async with container.database.sessions.begin() as session:
        for run_id in recovered_ids:
            claim = claims[run_id]
            assert not await container.runs.project(session, run_id, claim.attempt_id, 'late result')
            with pytest.raises(AppError) as fenced:
                await container.runs.lock_execution(session, run_id, claim.attempt_id)
            assert fenced.value.code == 'execution_lost'


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_started', [False, True])
async def test_recovery_other_key_completion_unknown_and_repeat(
    admin: httpx.AsyncClient, model_id: str, app: object, monkeypatch: pytest.MonkeyPatch, provider_started: bool
) -> None:
    container = app.state.container
    _, key_a = await provision(admin, model_id)
    _, key_b = await provision(admin, model_id)
    recovered_a, identity = await admitted(container, key_a['api_key'], model_id)
    recovered_b, _ = await admitted(container, key_b['api_key'], model_id)
    live_a, _ = await admitted(container, key_a['api_key'], model_id)
    live_b, _ = await admitted(container, key_b['api_key'], model_id)
    recovered_ids = {recovered_a.id, recovered_b.id}
    claims = {}
    async with container.database.sessions.begin() as session:
        for run in (recovered_a, recovered_b, live_a, live_b):
            claim = await container.runs.claim(session, run.id, 60)
            assert claim is not None
            claims[run.id] = claim
            if run.id not in recovered_ids or provider_started:
                assert await container.runs.start_provider(session, claim.id, claim.attempt_id)
        await session.flush()
        await session.execute(
            text("UPDATE runtime_runs SET lease_until=now()-interval '1 second' WHERE id IN (:a,:b)"),
            {'a': recovered_a.id, 'b': recovered_b.id},
        )
    first_settled = asyncio.Event()
    live_key_locked = asyncio.Event()
    first_id: list[UUID] = []
    settlement_counts: dict[UUID, int] = {}
    original_settle = UsageService.settle

    async def pausing_settle(self: UsageService, session: AsyncSession, run_id: UUID, **kwargs: object) -> UsageOutcome:
        outcome = await original_settle(self, session, run_id, **kwargs)
        settlement_counts[run_id] = settlement_counts.get(run_id, 0) + 1
        if run_id in recovered_ids and not first_id:
            first_id.append(run_id)
            first_settled.set()
            await asyncio.wait_for(live_key_locked.wait(), 3)
        return outcome

    monkeypatch.setattr(UsageService, 'settle', pausing_settle)
    finished_id: list[UUID] = []

    async def live_finish() -> None:
        await asyncio.wait_for(first_settled.wait(), 3)
        live, key = (live_b, key_b) if first_id[0] == recovered_a.id else (live_a, key_a)
        finished_id.append(live.id)
        async with container.database.sessions.begin() as session:
            await container.runs.lock_execution(session, live.id, claims[live.id].attempt_id)
            await session.scalar(select(Invocation).where(Invocation.run_id == live.id).with_for_update())
            await session.scalar(select(DeveloperKey).where(DeveloperKey.id == UUID(key['id'])).with_for_update())
            live_key_locked.set()
            usage = await original_settle(container.usage, session, live.id, input_tokens=8, output_tokens=2)
            await container.runs.finish(
                session,
                live.id,
                claims[live.id].attempt_id,
                status='completed',
                content='normal result',
                usage=RunUsage(**usage.model_dump()),
                finish_reason='stop',
            )

    results = await asyncio.wait_for(
        asyncio.gather(recover_expired(container), live_finish(), return_exceptions=True), 8
    )
    assert not [result for result in results if isinstance(result, BaseException)], results

    before = await recovery_state(container, identity.owner_id, recovered_ids, finished_id[0], provider_started)
    await recover_expired(container)
    await recover_expired(container)
    assert await recovery_state(container, identity.owner_id, recovered_ids, finished_id[0], provider_started) == before
    assert all(settlement_counts[run_id] == 1 for run_id in recovered_ids)
    await assert_recovered_attempts_fenced(container, recovered_ids, claims)


@pytest.mark.asyncio
async def test_recovery_commits_each_run_atomically_and_rolls_back_failed_settlement(
    admin: httpx.AsyncClient, model_id: str, app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = app.state.container
    _, key = await provision(admin, model_id)
    first, identity = await admitted(container, key['api_key'], model_id)
    second, _ = await admitted(container, key['api_key'], model_id)
    async with container.database.sessions.begin() as session:
        for run in (first, second):
            assert await container.runs.claim(session, run.id, 60) is not None
        await session.flush()
        await session.execute(text("UPDATE runtime_runs SET lease_until=now()-interval '1 second'"))
    original_settle = UsageService.settle
    order: list[UUID] = []

    async def fail_second(self: UsageService, session: AsyncSession, run_id: UUID, **kwargs: object) -> UsageOutcome:
        outcome = await original_settle(self, session, run_id, **kwargs)
        order.append(run_id)
        if len(order) == 2:
            raise RuntimeError('settlement commit failed')
        return outcome

    with monkeypatch.context() as patched:
        patched.setattr(UsageService, 'settle', fail_second)
        with pytest.raises(RuntimeError, match='settlement commit failed'):
            await recover_expired(container)
    async with container.database.sessions() as session:
        committed = await session.get(Run, order[0])
        rolled_back = await session.get(Run, order[1])
        inv_committed = await session.scalar(select(Invocation).where(Invocation.run_id == order[0]))
        inv_rolled_back = await session.scalar(select(Invocation).where(Invocation.run_id == order[1]))
        account = await session.get(UsageAccount, identity.owner_id)
        assert committed.status == 'interrupted' and inv_committed.status == 'known'
        assert rolled_back.status == 'running' and inv_rolled_back.status == 'pending'
        assert account.reserved_tokens == inv_rolled_back.reserved_tokens and account.used_tokens == 0
    await recover_expired(container)
    await recover_expired(container)
    async with container.database.sessions() as session:
        account = await session.get(UsageAccount, identity.owner_id)
        assert account.reserved_tokens == 0 and account.used_tokens == 0
        assert {row.status for row in await session.scalars(select(Run))} == {'interrupted'}
        assert {row.status for row in await session.scalars(select(Invocation))} == {'known'}
