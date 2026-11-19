"""Stream progress, native cancellation, durable partial results and no implicit retries."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_chat_runs import admit
from tests.core.integration.test_work_agent_execution import consume, turn, workspace_model
from tests.core.integration.test_work_agent_execution import (
    workspace_provider as workspace_provider,  # noqa: PLC0414 - register the shared fixture
)
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.models.public import AgentModelMessage, ModelBinding, ModelDelta, ModelMessage, ModelProvider
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run

TIMEOUTS = {
    'provider_timeout_seconds': 1,
    'provider_first_response_timeout_seconds': 0.3,
    'provider_idle_timeout_seconds': 0.3,
    'provider_total_timeout_seconds': 3,
}


@pytest.fixture
def settings(settings: Settings, request: pytest.FixtureRequest) -> Settings:
    return Settings.model_validate({**settings.model_dump(), **getattr(request, 'param', {})})


class ScriptedProvider:
    def __init__(self, mode: str) -> None:
        self.mode, self.calls, self.closed = mode, 0, False
        self.started = asyncio.Event()

    async def validate(self) -> None:
        pass

    async def stream_agent(
        self, _messages: tuple[AgentModelMessage, ...], _tools: tuple[dict[str, object], ...]
    ) -> AsyncIterator[ModelDelta]:
        async for delta in self.stream(()):
            yield delta

    async def stream(self, _messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
        self.calls += 1
        try:
            self.started.set()
            if self.mode == 'silent':
                await asyncio.Event().wait()
            if self.mode == 'heartbeats':
                while True:
                    yield ModelDelta()
                    await asyncio.sleep(0.06)
            yield ModelDelta(reasoning='正在分析。')
            if self.mode == 'idle':
                yield ModelDelta(text='已收到的部分正文。')
                await asyncio.Event().wait()
            for _ in range(16):
                await asyncio.sleep(0.09)
                yield ModelDelta(reasoning='继续分析。')
            yield ModelDelta('完整正文', finish_reason='stop', input_tokens=8, output_tokens=2)
        finally:
            self.closed = True


def install(app: FastAPI, monkeypatch: pytest.MonkeyPatch, mode: str) -> ScriptedProvider:
    provider = ScriptedProvider(mode)

    async def factory(_binding: ModelBinding) -> ModelProvider:
        return provider

    monkeypatch.setattr(app.state.container.run_executor, '_provider_factory', factory)
    return provider


@pytest.mark.parametrize('settings', [TIMEOUTS], indirect=True)
async def test_live_reasoning_stream_exceeds_the_old_timeout_and_completes_once(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = install(app, monkeypatch, 'active')
    cid, run = await admit(admin, model_id)
    await execute_run(app.state.container, UUID(run['id']))
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'completed' and result['text'] == '完整正文', result
    assert result['reasoning'].startswith('正在分析。')
    assert result['usage'] == {'status': 'known', 'input_tokens': 8, 'output_tokens': 2}
    assert provider.calls == 1 and provider.closed
    assert (await admin.get(f'/api/chat/conversations/{cid}/messages')).json()[-1]['content'] == '完整正文'


@pytest.mark.parametrize('settings', [TIMEOUTS], indirect=True)
@pytest.mark.parametrize(
    'mode,code',
    [
        ('silent', 'model_first_response_timeout'),
        ('heartbeats', 'model_first_response_timeout'),
        ('idle', 'model_idle_timeout'),
    ],
)
async def test_silence_is_classified_and_an_existing_run_is_not_retried(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    code: str,
) -> None:
    provider = install(app, monkeypatch, mode)
    key = str(uuid4())
    cid, run = await admit(admin, model_id, client_request_id=key)
    await execute_run(app.state.container, UUID(run['id']))
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'failed' and result['error']['code'] == code, result
    assert result['usage']['status'] == 'unknown'
    if mode == 'idle':
        assert result['text'] == '已收到的部分正文。' and result['reasoning'] == '正在分析。'
    repeated = await admin.post(
        f'/api/chat/conversations/{cid}/runs', json={'client_request_id': key, 'model_id': model_id, 'message': '你好'}
    )
    assert repeated.status_code == 202 and repeated.json()['id'] == run['id'], repeated.text
    await execute_run(app.state.container, UUID(run['id']))
    assert provider.calls == 1 and provider.closed


@pytest.mark.parametrize('settings', [{**TIMEOUTS, 'provider_total_timeout_seconds': 1}], indirect=True)
async def test_total_budget_interrupts_a_live_stream_and_preserves_partial_content(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = install(app, monkeypatch, 'active')
    _, run = await admit(admin, model_id)
    await execute_run(app.state.container, UUID(run['id']))
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'interrupted' and result['error']['code'] == 'model_deadline_exceeded', result
    assert result['reasoning'].startswith('正在分析。') and result['usage']['status'] == 'unknown'
    assert provider.calls == 1 and provider.closed


@pytest.mark.parametrize('settings', [TIMEOUTS], indirect=True)
async def test_user_cancellation_still_closes_an_active_provider_stream(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = install(app, monkeypatch, 'active')
    _, run = await admit(admin, model_id)
    execution = asyncio.create_task(execute_run(app.state.container, UUID(run['id'])))
    await asyncio.wait_for(provider.started.wait(), timeout=2)
    response = await admin.post(f'/api/runs/{run["id"]}/cancel', json={})
    assert response.status_code == 200, response.text
    await asyncio.wait_for(execution, timeout=2)
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'cancelled' and provider.closed and provider.calls == 1, result


@pytest.mark.parametrize('settings', [TIMEOUTS], indirect=True)
async def test_workspace_keeps_model_idle_reason_and_partial_reply_as_a_resumable_task(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    provider = install(app, monkeypatch, 'idle')
    created = await admin.post(
        '/api/work/tasks', json={'client_request_id': str(uuid4()), 'model_id': model_id, 'goal': '检查持续输出'}
    )
    assert created.status_code == 201, created.text
    task = created.json()
    run = await turn(admin, task, task['goal'], initial=True)
    await consume(app)
    result = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert result['status'] == 'interrupted' and result['error']['code'] == 'model_idle_timeout', result
    assert result['text'] == '已收到的部分正文。' and result['usage']['status'] == 'unknown'
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert detail['execution_state'] == 'paused' and provider.calls == 1 and provider.closed


@pytest.mark.parametrize('settings', [TIMEOUTS], indirect=True)
async def test_workspace_child_model_deadline_cannot_outlive_the_parent_budget(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    container = app.state.container
    container.work_agent.context.policy = replace(container.work_agent.context.policy, run_seconds=1)
    provider = install(app, monkeypatch, 'active')
    created = await admin.post(
        '/api/work/tasks', json={'client_request_id': str(uuid4()), 'model_id': model_id, 'goal': '检查父任务预算'}
    )
    task = created.json()
    run = await turn(admin, task, task['goal'], initial=True)
    await consume(app)
    result = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert result['status'] == 'interrupted', result
    assert result['error']['code'] in {
        'work_budget_exhausted',
        'model_deadline_exceeded',
        'execution_deadline_exceeded',
    }
    async with container.database.sessions() as session:
        children = list(await session.scalars(select(Run).where(Run.resource_type == 'work_agent_model')))
    assert len(children) == 1 and children[0].payload['execution_deadline']
    assert provider.calls <= 1
    if provider.calls:
        assert provider.closed
    else:
        assert not children[0].provider_started and children[0].usage_status == 'known'


@pytest.mark.parametrize('settings', [{**TIMEOUTS, 'provider_total_timeout_seconds': 1}], indirect=True)
async def test_queued_request_keeps_its_original_deadline_and_never_starts_a_late_paid_call(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = install(app, monkeypatch, 'active')
    _, run = await admit(admin, model_id)
    await asyncio.sleep(1.05)
    await execute_run(app.state.container, UUID(run['id']))
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'interrupted' and result['error']['code'] == 'execution_deadline_exceeded', result
    assert provider.calls == 0 and result['usage'] == {'status': 'known', 'input_tokens': 0, 'output_tokens': 0}


@pytest.mark.parametrize('settings', [TIMEOUTS], indirect=True)
async def test_timeout_watchdog_survives_a_blocked_database_heartbeat(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = install(app, monkeypatch, 'silent')
    entered = asyncio.Event()

    async def blocked_lease(*_args: object, **_kwargs: object) -> str:
        entered.set()
        await asyncio.Event().wait()
        return 'active'

    monkeypatch.setattr(app.state.container.runs, 'control', blocked_lease)
    _, run = await admit(admin, model_id)
    await asyncio.wait_for(execute_run(app.state.container, UUID(run['id'])), timeout=2)
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert entered.is_set() and result['error']['code'] == 'model_first_response_timeout', result
    assert provider.calls == 1 and provider.closed
