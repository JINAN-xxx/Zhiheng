import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from tests.core.integration.test_chat_runs import admit
from tests.core.provider import ProviderServer


async def named_model(admin: httpx.AsyncClient, provider: ProviderServer, upstream: str) -> str:
    connection = (
        await admin.post(
            '/api/models/connections',
            json={
                'name': upstream,
                'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
                'api_key': 'local-test-key',
            },
        )
    ).json()
    return (
        await admin.post(
            '/api/models',
            json={
                'name': upstream,
                'connection_id': connection['id'],
                'upstream_model': upstream,
                'max_output_tokens': 64,
            },
        )
    ).json()['id']


async def test_real_arq_worker_consumes_transactional_outbox(
    admin: httpx.AsyncClient,
    model_id: str,
    app: object,
    provider: ProviderServer,
) -> None:
    from zhiheng.worker.main import build_worker

    _conversation, run = await admit(admin, model_id)
    worker = build_worker(app.state.container, burst=True)
    try:
        await asyncio.wait_for(worker.async_run(), timeout=10)
    finally:
        await worker.close()
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'completed', result
    assert len(provider.requests) == 1


async def test_silent_provider_can_be_cancelled_before_timeout(
    admin: httpx.AsyncClient,
    app: object,
    provider: ProviderServer,
) -> None:
    from zhiheng.worker.execution import execute_run

    model_id = await named_model(admin, provider, 'silent')
    _conversation, run = await admit(admin, model_id)
    execution = asyncio.create_task(execute_run(app.state.container, UUID(run['id'])))
    try:
        for _ in range(100):
            if provider.requests:
                break
            await asyncio.sleep(0.01)
        assert provider.requests
        cancelled = await admin.post(f'/api/runs/{run["id"]}/cancel', json={})
        assert cancelled.status_code == 200
        await asyncio.wait_for(execution, timeout=1.5)
        result = (await admin.get(f'/api/runs/{run["id"]}')).json()
        assert result['status'] == 'cancelled'
        assert result['usage']['status'] == 'unknown'
        assert (await admin.get('/api/usage')).json()['reserved_tokens'] > 0
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)


async def test_expired_attempt_is_fenced_and_keeps_unknown_reservation(
    admin: httpx.AsyncClient,
    model_id: str,
    app: object,
) -> None:
    from zhiheng.shared.errors import AppError
    from zhiheng.worker.main import recover_expired

    container = app.state.container
    _conversation, run = await admit(admin, model_id)
    async with container.database.sessions.begin() as session:
        claim = await container.runs.claim(session, UUID(run['id']), 3)
        assert claim is not None
        assert await container.runs.start_provider(session, claim.id, claim.attempt_id)
        await session.execute(text("UPDATE runtime_runs SET lease_until = now() - interval '1 second'"))
    await recover_expired(container)
    async with container.database.sessions.begin() as session:
        assert await container.runs.project(session, claim.id, claim.attempt_id, 'late text') is False
        with pytest.raises(AppError, match='失效'):
            await container.runs.lock_execution(session, claim.id, claim.attempt_id)
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'interrupted' and result['usage']['status'] == 'unknown'
    usage = (await admin.get('/api/usage')).json()
    assert usage['reserved_tokens'] > 0 and usage['records'][0]['charged_tokens'] is None


async def test_success_without_usage_remains_unknown(
    admin: httpx.AsyncClient, app: object, provider: ProviderServer
) -> None:
    from zhiheng.worker.execution import execute_run

    model_id = await named_model(admin, provider, 'no-usage')
    _conversation, run = await admit(admin, model_id)
    await execute_run(app.state.container, UUID(run['id']))
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'completed' and result['text'] == '你好'
    assert result['usage']['status'] == 'unknown'
    usage = (await admin.get('/api/usage')).json()
    assert usage['reserved_tokens'] > 0 and usage['records'][0]['charged_tokens'] is None


async def test_rejected_provider_is_not_retried_or_charged(
    admin: httpx.AsyncClient, app: object, provider: ProviderServer
) -> None:
    from zhiheng.worker.execution import execute_run

    model_id = await named_model(admin, provider, 'rejected')
    _conversation, run = await admit(admin, model_id)
    await execute_run(app.state.container, UUID(run['id']))
    result = await admin.get(f'/api/runs/{run["id"]}')
    assert result.json()['status'] == 'failed'
    assert 'private-provider-details' not in result.text
    usage = (await admin.get('/api/usage')).json()
    assert usage['reserved_tokens'] == 0 and usage['used_tokens'] == 0
    assert len(provider.requests) == 1


async def test_connection_test_uses_the_same_governance_without_creating_chat(
    admin: httpx.AsyncClient,
    model_id: str,
    app: object,
) -> None:
    from zhiheng.worker.execution import execute_run

    result = await admin.post(f'/api/models/{model_id}/test', json={'client_request_id': str(uuid4())})
    assert result.status_code == 202, result.text
    await execute_run(app.state.container, UUID(result.json()['id']))
    usage = (await admin.get('/api/usage')).json()
    assert usage['records'][0]['source'] == 'connection_test'
    assert usage['used_tokens'] == 10
    assert (await admin.get('/api/chat/conversations')).json() == []


async def test_premature_eof_does_not_create_complete_message_or_pollute_context(
    admin: httpx.AsyncClient,
    app: object,
    provider: ProviderServer,
) -> None:
    from zhiheng.worker.execution import execute_run

    model_id = await named_model(admin, provider, 'truncated')
    conversation_id, run = await admit(admin, model_id)
    await execute_run(app.state.container, UUID(run['id']))
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'failed'
    assert result['usage']['status'] == 'unknown'
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert [message['role'] for message in messages] == ['user']
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] > 0
    valid_model = await named_model(admin, provider, 'local-text')
    followup = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': valid_model,
            'message': 'follow up',
            'client_request_id': str(uuid4()),
        },
    )
    assert followup.status_code == 202
    await execute_run(app.state.container, UUID(followup.json()['id']))
    assert provider.requests[-1]['messages'] == [{'role': 'user', 'content': 'follow up'}]


async def test_shutdown_does_not_leave_a_late_terminal_writer(
    admin: httpx.AsyncClient,
    app: object,
    provider: ProviderServer,
) -> None:
    from zhiheng.worker.execution import execute_run

    model_id = await named_model(admin, provider, 'silent')
    _conversation, run = await admit(admin, model_id)
    container = app.state.container
    execution = asyncio.create_task(execute_run(container, UUID(run['id'])))
    try:
        for _ in range(100):
            if provider.requests:
                break
            await asyncio.sleep(0.01)
        assert provider.requests
        async with container.database.sessions.begin() as blocker:
            await blocker.execute(text('SELECT id FROM runtime_runs WHERE id=:id FOR UPDATE'), {'id': UUID(run['id'])})
            execution.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(execution, timeout=4)
            pending_finalizers = [
                task
                for task in asyncio.all_tasks()
                if not task.done()
                and getattr(task.get_coro(), '__qualname__', '').endswith('InferenceExecutor._finish')
            ]
            assert not pending_finalizers
        await asyncio.sleep(0.2)
        snapshot = (await admin.get(f'/api/runs/{run["id"]}')).json()
        assert snapshot['status'] == 'running'
    finally:
        execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)


@pytest.mark.parametrize(
    'upstream,expected_input,expected_output', [('partial-usage', 8, None), ('missing-input', None, 2)]
)
async def test_missing_raw_usage_counter_keeps_reservation_unknown(
    admin: httpx.AsyncClient,
    app: object,
    provider: ProviderServer,
    upstream: str,
    expected_input: int | None,
    expected_output: int | None,
) -> None:
    from zhiheng.worker.execution import execute_run

    model_id = await named_model(admin, provider, upstream)
    _conversation, run = await admit(admin, model_id)
    await execute_run(app.state.container, UUID(run['id']))
    snapshot = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert snapshot['status'] == 'completed'
    assert snapshot['usage'] == {'input_tokens': expected_input, 'output_tokens': expected_output, 'status': 'unknown'}
    usage = (await admin.get('/api/usage')).json()
    assert usage['reserved_tokens'] > 0
    assert usage['records'][0]['charged_tokens'] is None
