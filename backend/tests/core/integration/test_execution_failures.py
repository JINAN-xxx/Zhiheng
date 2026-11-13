from traceback import format_exception
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.core.billing_support import enable_free_billing
from tests.core.integration.test_chat_runs import admit
from tests.core.provider import ProviderServer
from zhiheng.modules.chat.orm import Conversation
from zhiheng.runtime.contracts import ClaimedRun
from zhiheng.shared.errors import AppError
from zhiheng.worker.execution import execute_run


def database_error(sqlstate: str) -> DBAPIError:
    class DatabaseFailure(Exception):
        pass

    original = DatabaseFailure('private-driver-detail')
    original.sqlstate = sqlstate
    return DBAPIError('SELECT private-statement', {'credential': 'private-parameter'}, original)


@pytest.mark.parametrize(
    ('sqlstate', 'error_code'),
    [
        ('40P01', 'execution_conflict'),
        ('40001', 'execution_conflict'),
        ('55P03', 'execution_conflict'),
        ('23503', 'execution_unavailable'),
    ],
)
async def test_database_failure_before_provider_is_classified_and_releases_usage(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    sqlstate: str,
    error_code: str,
) -> None:
    container = app.state.container
    _, run = await admit(admin, model_id)

    async def reject(_claim: ClaimedRun) -> None:
        raise database_error(sqlstate)

    monkeypatch.setattr(container.run_executor, '_before_provider', reject)
    await execute_run(container, UUID(run['id']))
    response = await admin.get(f'/api/runs/{run["id"]}')
    snapshot = response.json()
    assert snapshot['status'] == 'failed' and snapshot['error']['code'] == error_code
    assert snapshot['usage']['status'] == 'known' and not provider.requests
    usage = (await admin.get('/api/usage')).json()
    assert usage['reserved_tokens'] == 0 and usage['used_tokens'] == 0
    records = [record for record in caplog.records if getattr(record, 'run_id', None) == run['id']]
    assert len(records) == 1
    record = records[0]
    assert record.sqlstate == sqlstate and record.execution_stage == 'before_provider'
    assert record.attempt_id == snapshot['attempt_id'] and record.exception_frames
    assert f'sqlstate={sqlstate}' in record.getMessage()
    for secret in ['private-driver-detail', 'private-statement', 'private-parameter']:
        assert secret not in caplog.text and secret not in response.text


async def test_authorization_failure_keeps_its_public_reason(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    container = app.state.container
    conversation, run = await admit(admin, model_id)
    async with container.database.sessions.begin() as session:
        row = await session.get(Conversation, UUID(conversation))
        assert row is not None
        row.archived = True
    await execute_run(container, UUID(run['id']))
    snapshot = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert snapshot['error']['code'] == 'conversation_archived'
    assert not provider.requests


async def test_internal_preparation_failure_does_not_claim_a_provider_failure(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    _, run = await admit(admin, model_id)

    async def reject(_claim: ClaimedRun) -> None:
        raise RuntimeError('private-internal-detail')

    monkeypatch.setattr(container.run_executor, '_before_provider', reject)
    await execute_run(container, UUID(run['id']))
    response = await admin.get(f'/api/runs/{run["id"]}')
    assert response.json()['error']['code'] == 'execution_failed'
    assert 'private-internal-detail' not in response.text and not provider.requests


async def test_database_failure_after_provider_preserves_unknown_usage_without_retry(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await enable_free_billing(admin)
    container = app.state.container
    _, run = await admit(admin, model_id)

    async def fail_projection(*_args: object, **_kwargs: object) -> None:
        raise database_error('40P01')

    monkeypatch.setattr(container.runs, 'project', fail_projection)
    await execute_run(container, UUID(run['id']))
    snapshot = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert snapshot['status'] == 'failed' and snapshot['error']['code'] == 'execution_conflict'
    assert snapshot['usage']['status'] == 'unknown'
    assert '重试' not in snapshot['error']['message']
    usage = (await admin.get('/api/usage')).json()
    assert usage['reserved_tokens'] > 0 and len(provider.requests) == 1
    records = [record for record in caplog.records if getattr(record, 'run_id', None) == run['id']]
    assert records[0].execution_stage == 'provider' and records[0].sqlstate == '40P01'
    assert 'private-parameter' not in caplog.text


async def test_application_failure_after_provider_does_not_recommend_repeating_the_call(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    _, run = await admit(admin, model_id)

    async def fail_projection(*_args: object, **_kwargs: object) -> None:
        raise AppError('execution_conflict', '状态更新冲突，请重试', 409)

    monkeypatch.setattr(container.runs, 'project', fail_projection)
    await execute_run(container, UUID(run['id']))
    snapshot = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert snapshot['error']['code'] == 'execution_conflict'
    assert '重试' not in snapshot['error']['message']
    assert snapshot['usage']['status'] == 'unknown' and len(provider.requests) == 1


async def test_claim_database_failure_stays_pending_and_logs_safe_diagnostics(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    container = app.state.container
    _, run = await admit(admin, model_id)

    async def fail_claim(*_args: object, **_kwargs: object) -> None:
        raise database_error('40P01')

    monkeypatch.setattr(container.runs, 'claim', fail_claim)
    with pytest.raises(RuntimeError, match='claim') as raised:
        await execute_run(container, UUID(run['id']))
    traceback = ''.join(format_exception(type(raised.value), raised.value, raised.value.__traceback__))
    assert all(
        secret not in traceback for secret in ['private-driver-detail', 'private-statement', 'private-parameter']
    )
    snapshot = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert snapshot['status'] == 'pending' and snapshot['attempt_id'] is None and not provider.requests
    records = [record for record in caplog.records if getattr(record, 'run_id', None) == run['id']]
    assert records[0].execution_stage == 'claim' and records[0].sqlstate == '40P01'
    assert 'private-parameter' not in caplog.text


async def test_settlement_database_failure_keeps_recovery_ownership_and_does_not_repeat_provider(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await enable_free_billing(admin)
    container = app.state.container
    _, run = await admit(admin, model_id)
    settle = container.usage.settle

    async def fail_settle(*_args: object, **_kwargs: object) -> None:
        raise database_error('40P01')

    monkeypatch.setattr(container.usage, 'settle', fail_settle)
    with pytest.raises(RuntimeError, match='settlement') as raised:
        await execute_run(container, UUID(run['id']))
    traceback = ''.join(format_exception(type(raised.value), raised.value, raised.value.__traceback__))
    assert all(
        secret not in traceback for secret in ['private-driver-detail', 'private-statement', 'private-parameter']
    )
    snapshot = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert snapshot['status'] == 'running' and len(provider.requests) == 1
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] > 0
    monkeypatch.setattr(container.usage, 'settle', settle)
    async with container.database.sessions.begin() as session:
        await session.execute(
            text("UPDATE runtime_runs SET lease_until=now()-interval '1 second' WHERE id=:id"),
            {'id': UUID(run['id'])},
        )
    await container.run_recovery.recover_expired()
    recovered = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert recovered['status'] == 'interrupted' and recovered['usage']['status'] == 'unknown'
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] > 0 and len(provider.requests) == 1
    records = [record for record in caplog.records if getattr(record, 'run_id', None) == run['id']]
    assert records[0].execution_stage == 'settlement' and records[0].sqlstate == '40P01'
    assert 'private-parameter' not in caplog.text
