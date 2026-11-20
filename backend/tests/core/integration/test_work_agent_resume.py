"""Explicit continuation retains requests around trusted graph checkpoints."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_work_agent_execution import (
    consume,
    turn,
    workspace_model,
)
from tests.core.integration.test_work_agent_execution import (
    workspace_provider as workspace_provider,  # noqa: PLC0414 - explicitly register shared pytest fixtures
)
from tests.core.integration.test_work_agent_execution import (
    workspace_settings as workspace_settings,  # noqa: PLC0414 - explicitly register shared pytest fixtures
)
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.application.work.tools import WorkspaceTools
from zhiheng.shared.errors import AppError


async def continue_task(admin: httpx.AsyncClient, task_id: str) -> dict[str, object]:
    task = (await admin.get(f'/api/work/tasks/{task_id}')).json()
    response = await admin.post(
        f'/api/work/tasks/{task_id}/runs',
        json={
            'client_request_id': str(uuid4()),
            'expected_version': task['version'],
            'resume': True,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def test_resume_reseeds_the_exact_followup_when_no_trusted_checkpoint_captured_it(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[str] = []

    def response(body: dict[str, object]) -> dict[str, object]:
        if body.get('tools'):
            request = next(item['content'] for item in reversed(body['messages']) if item['role'] == 'user')
            attempts.append(request)
            if len(attempts) == 2:
                return workspace_provider.tool('invalid_tool', {}, 'invalid-once')
            return {'role': 'assistant', 'content': '已处理：' + request}
        return {'role': 'assistant', 'content': '本轮处理完成。'}

    monkeypatch.setattr(workspace_provider, 'response', response)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': 'ORIGINAL TASK',
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    await turn(admin, task, 'ORIGINAL TASK', initial=True)
    await consume(app)
    task = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    failed = await turn(admin, task, 'LATEST REVISION: use the updated title')
    await consume(app)
    snapshot = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{failed["id"]}')).json()
    assert snapshot['status'] == 'failed', snapshot
    resumed = await continue_task(admin, task['id'])
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed["id"]}')).json()
    assert final['status'] == 'completed', final
    assert attempts == [
        'ORIGINAL TASK',
        'LATEST REVISION: use the updated title',
        'LATEST REVISION: use the updated title',
    ]
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert [item['content'] for item in detail['messages'] if item['role'] == 'user'] == attempts[:2]


async def test_resume_of_ended_graph_reviews_again_after_finalization_failed(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_requests: list[dict[str, object]] = []
    finalizations = 0
    finalize = WorkspaceTools.finalize_outputs

    async def finish(self: WorkspaceTools) -> None:
        nonlocal finalizations
        await finalize(self)
        finalizations += 1
        if finalizations == 1:
            raise AppError('work_artifact_invalid', 'test finalization acknowledgement failure', 422)

    def response(body: dict[str, object]) -> dict[str, object]:
        if body.get('tools'):
            agent_requests.append(body)
            return {'role': 'assistant', 'content': '已核对任务。'}
        raise AssertionError('A completed answer must not be sent to a separate delivery model')

    monkeypatch.setattr(workspace_provider, 'response', response)
    monkeypatch.setattr(WorkspaceTools, 'finalize_outputs', finish)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    created = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': '核对任务中的记录',
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    first = await turn(admin, task, task['goal'], initial=True)
    await consume(app)
    assert (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{first["id"]}')).json()['status'] == 'failed'
    resumed = await continue_task(admin, task['id'])
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed["id"]}')).json()
    assert final['status'] == 'completed', final
    assert len(agent_requests) == 2 and finalizations == 2
    latest = next(item['content'] for item in reversed(agent_requests[-1]['messages']) if item['role'] == 'user')
    assert '尚未解决的失败' in latest
