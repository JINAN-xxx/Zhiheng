"""Terminal task aggregates follow child recovery without another billing settlement."""

from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text, update

from tests.core.billing_support import enable_free_billing
from zhiheng.application.execution.admission import AdmissionIdentity
from zhiheng.bootstrap.container import Container
from zhiheng.modules.models.public import ModelMessage, ModelRequest
from zhiheng.runtime.contracts import ClaimedRun, RunUsage
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow


async def parent_run(admin: httpx.AsyncClient, app: FastAPI, model_id: str) -> tuple[dict[str, object], ClaimedRun]:
    await enable_free_billing(admin)
    created = await admin.post(
        '/api/work/tasks',
        json={
            'model_id': model_id,
            'goal': 'Recover a task',
            'client_request_id': str(uuid4()),
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    admitted = await admin.post(
        f'/api/work/tasks/{task["id"]}/runs',
        json={
            'client_request_id': task['create_client_request_id'],
            'expected_version': task['version'],
            'message': task['goal'],
        },
    )
    assert admitted.status_code == 202, admitted.text
    async with app.state.container.database.sessions.begin() as session:
        claim = await app.state.container.runs.claim(session, UUID(admitted.json()['id']), 60)
        assert claim is not None
    return task, claim


async def child_run(container: Container, parent: ClaimedRun, model_id: str) -> UUID:
    async with container.database.sessions.begin() as session:
        await container.runs.lock_execution(session, parent.id, parent.attempt_id)
        binding = await container.models.bindings.resolve(session, UUID(model_id))
        run = await container.run_admission.reserve(
            session,
            identity=AdmissionIdentity(owner_id=parent.owner_id, billing_owner_id=parent.owner_id),
            resource_type='work_agent_model',
            resource_id=parent.resource_id,
            client_request_id=uuid4(),
            fingerprint=uuid4().hex,
            request=ModelRequest(
                binding=binding,
                messages=(ModelMessage('user', 'A recovery call'),),
                parent_run_id=parent.id,
                parent_attempt_id=parent.attempt_id,
                required_modules=('work',),
            ),
        )
    return run.id


async def complete_child(container: Container, child_id: UUID) -> None:
    async with container.database.sessions.begin() as session:
        claim = await container.runs.claim(session, child_id, 60)
        assert claim is not None
        assert await container.runs.start_provider(session, child_id, claim.attempt_id)
        await container.runs.finish(
            session,
            child_id,
            claim.attempt_id,
            status='completed',
            content='known reply',
            usage=RunUsage(input_tokens=5, output_tokens=2, status='known'),
        )
        await container.usage.settle(session, child_id, input_tokens=5, output_tokens=2)


async def finish_parent(container: Container, parent: ClaimedRun) -> None:
    async with container.database.sessions.begin() as session:
        snapshot = await container.runs.finish(
            session,
            parent.id,
            parent.attempt_id,
            status='interrupted',
            content='Saved partial task',
            usage=await container.runs.aggregate_children(session, parent.id),
            result={'execution_state': 'paused'},
        )
        assert snapshot.usage.status == 'pending'
        await container.work_agent.context.tasks.project_snapshot(session, snapshot)


async def billing_facts(container: Container) -> dict[str, list[object]]:
    tables = (
        'developer_accounts',
        'developer_invocations',
        'billing_accounts',
        'billing_reservations',
        'billing_ledger',
    )
    async with container.database.sessions() as session:
        return {
            name: list(
                await session.scalars(
                    text(f'SELECT to_jsonb(value) FROM {name} AS value ORDER BY to_jsonb(value)::text')
                )
            )
            for name in tables
        }


async def assert_parent_usage(
    admin: httpx.AsyncClient,
    task: dict[str, object],
    parent: ClaimedRun,
    expected: dict[str, object],
) -> dict[str, object]:
    snapshot = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{parent.id}')).json()
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert snapshot['status'] == 'interrupted' and snapshot['usage'] == expected
    assert detail['execution_state'] == 'paused' and detail['current_run_id'] is None
    message = next(
        item for item in detail['messages'] if item['role'] == 'assistant' and item['run_id'] == str(parent.id)
    )
    assert message['usage'] == expected and message['content'] == 'Saved partial task'
    return snapshot


async def test_orphan_pending_child_cancellation_repairs_parent_without_double_charging(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    container = app.state.container
    task, parent = await parent_run(admin, app, model_id)
    known, pending = await child_run(container, parent, model_id), await child_run(container, parent, model_id)
    await complete_child(container, known)
    await finish_parent(container, parent)
    await container.work_agent.reconcile()
    snapshot = await assert_parent_usage(
        admin, task, parent, {'input_tokens': 5, 'output_tokens': 2, 'status': 'known'}
    )
    async with container.database.sessions() as session:
        child = await container.runs.projection(session, pending)
        assert child.status == 'cancelled' and child.usage == RunUsage(input_tokens=0, output_tokens=0, status='known')
    assert (await admin.get('/api/usage')).json()['used_tokens'] == 7
    after = await billing_facts(container)
    await container.work_agent.reconcile()
    assert await billing_facts(container) == after
    repeated = await assert_parent_usage(admin, task, parent, snapshot['usage'])
    assert repeated['revision'] == snapshot['revision']


@pytest.mark.parametrize('provider_started', [False, True], ids=['before-provider', 'usage-unknown'])
async def test_expired_child_recovery_repairs_pending_parent_without_mutating_billing(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider_started: bool,
) -> None:
    container = app.state.container
    task, parent = await parent_run(admin, app, model_id)
    known, active = await child_run(container, parent, model_id), await child_run(container, parent, model_id)
    await complete_child(container, known)
    async with container.database.sessions.begin() as session:
        child = await container.runs.claim(session, active, 60)
        assert child is not None
        if provider_started:
            assert await container.runs.start_provider(session, active, child.attempt_id)
    await finish_parent(container, parent)
    async with container.database.sessions() as session:
        assert await container.runs.pending_parent_usage(session, resource_type='work_agent') == []
    async with container.database.sessions.begin() as session:
        await session.execute(update(Run).where(Run.id == active).values(lease_until=utcnow() - timedelta(seconds=1)))
    await container.run_recovery.recover_expired()
    recovered_billing = await billing_facts(container)
    await container.work_agent.reconcile()
    expected = (
        {'input_tokens': None, 'output_tokens': None, 'status': 'unknown'}
        if provider_started
        else {'input_tokens': 5, 'output_tokens': 2, 'status': 'known'}
    )
    first = await assert_parent_usage(admin, task, parent, expected)
    assert await billing_facts(container) == recovered_billing
    async with container.database.sessions() as session:
        known_snapshot = await container.runs.projection(session, known)
        recovered = await container.runs.projection(session, active)
        assert known_snapshot.usage == RunUsage(input_tokens=5, output_tokens=2, status='known')
        assert recovered.usage.status == ('unknown' if provider_started else 'known')
    await container.work_agent.reconcile()
    assert await billing_facts(container) == recovered_billing
    second = await assert_parent_usage(admin, task, parent, expected)
    assert first['revision'] == second['revision']
    usage = (await admin.get('/api/usage')).json()
    assert usage['used_tokens'] == 7
    assert (usage['reserved_tokens'] > 0) is provider_started


async def test_pending_parent_query_is_bounded_and_preserves_known_unknown_and_active_runs(app: FastAPI) -> None:
    container = app.state.container
    async with container.database.sessions.begin() as session:
        users = await container.identity.directory(session, search='admin')
        values = []
        for resource_type, status, usage_status in (
            ('work_agent', 'interrupted', 'pending'),
            ('work_agent', 'failed', 'pending'),
            ('work_agent', 'cancelled', 'unknown'),
            ('work_agent', 'completed', 'known'),
            ('work_agent', 'running', 'pending'),
            ('chat', 'failed', 'pending'),
        ):
            run = await container.runs.create(
                session,
                owner_id=users[0].id,
                resource_type=resource_type,
                resource_id=uuid4(),
                request_id=uuid4(),
                fingerprint=uuid4().hex,
                payload={},
            )
            await session.execute(update(Run).where(Run.id == run.id).values(status=status, usage_status=usage_status))
            values.append(run.id)
    async with container.database.sessions() as session:
        limited = await container.runs.pending_parent_usage(session, resource_type='work_agent', limit=1)
        assert len(limited) == 1 and limited[0] in values[:2]
        all_pending = await container.runs.pending_parent_usage(session, resource_type='work_agent', limit=10000)
        assert set(all_pending) == set(values[:2])
        before = await session.scalar(select(Run).where(Run.id == values[2]))
        assert before is not None
        revision = before.revision
    async with container.database.sessions.begin() as session:
        snapshot = await container.runs.project_child_usage(session, values[2], pending_only=True)
        assert snapshot.usage.status == 'unknown' and snapshot.revision == revision
