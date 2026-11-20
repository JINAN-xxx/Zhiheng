"""Direct answers, visible streaming, search stop conditions and native accounting."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_model_timeouts import ScriptedProvider
from tests.core.integration.test_work_agent_execution import consume, turn, workspace_model
from tests.core.integration.test_work_agent_execution import workspace_provider as workspace_provider  # noqa: PLC0414
from tests.core.integration.test_workspace_search import SOURCE_URL, search_provider
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.modules.models.public import ModelBinding, ModelDelta, ModelMessage, ModelProvider
from zhiheng.modules.work.orm import WorkSandboxLease
from zhiheng.runtime.orm import Run


async def create_task(admin: httpx.AsyncClient, model_id: str, goal: str) -> dict[str, object]:
    response = await admin.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': goal,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def current_messages(body: dict[str, object]) -> list[dict[str, object]]:
    messages = body['messages']
    last = max(index for index, message in enumerate(messages) if message['role'] == 'user')
    return messages[last:]


async def test_weather_knowledge_and_acknowledgement_are_delivered_without_a_second_rewriter(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []
    app.state.container.work_agent.context.web_search = search_provider(calls)
    replies = {
        '你知道opencv么': '知道，OpenCV 是开源计算机视觉库，Python 中通过 cv2 使用。',
        '你总结的还是不错的': '谢谢认可，有需要随时继续。',
    }

    def response(body: dict[str, object]) -> dict[str, object]:
        if not body.get('tools'):
            return {'role': 'assistant', 'content': '没有可下载成果，不能把草稿作为已核验结论。'}
        turn_messages = current_messages(body)
        user = turn_messages[0]['content']
        if user in replies:
            assert not any(message['role'] == 'tool' for message in turn_messages)
            return {'role': 'assistant', 'content': replies[user]}
        if not any(message['role'] == 'tool' for message in turn_messages):
            return WorkspaceProvider.tool('web_search', {'query': '贵阳天气今天'}, 'weather')
        return {'role': 'assistant', 'content': '已找到贵阳天气的公开资料：[天气来源](' + SOURCE_URL + ')。'}

    monkeypatch.setattr(workspace_provider, 'response', response)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id, '你查一下贵阳的天气')
    for index, question in enumerate(['你查一下贵阳的天气', *replies]):
        if index:
            task = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
        run = await turn(admin, task, question)
        await consume(app)
        final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
        assert final['status'] == 'completed', final
        if question in replies:
            assert final['text'] == replies[question]
            assert final['usage'] == {'input_tokens': 17, 'output_tokens': 3, 'status': 'known'}
        else:
            assert SOURCE_URL in final['text']
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert all(step['name'] != 'final' for step in detail['steps'])
    assert len(calls) == 1 and len(workspace_provider.requests) == 4
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WorkSandboxLease)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'work_agent_model'))
            == 4
        )


class ControlledStream(ScriptedProvider):
    def __init__(self) -> None:
        super().__init__('controlled')
        self.release = asyncio.Event()

    async def stream(self, _messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
        self.calls += 1
        try:
            self.started.set()
            yield ModelDelta(text='知道，OpenCV')
            await self.release.wait()
            yield ModelDelta(text=' 是计算机视觉库。', finish_reason='stop', input_tokens=17, output_tokens=3)
        finally:
            self.closed = True


async def test_main_answer_is_visible_while_the_model_is_still_streaming(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    provider = ControlledStream()

    async def factory(_binding: ModelBinding) -> ModelProvider:
        return provider

    monkeypatch.setattr(app.state.container.run_executor, '_provider_factory', factory)
    task = await create_task(admin, model_id, '你知道opencv么')
    run = await turn(admin, task, task['goal'])
    worker = asyncio.create_task(consume(app, seconds=30))
    try:
        await asyncio.wait_for(provider.started.wait(), timeout=10)
        for _ in range(100):
            shown = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
            if shown['text']:
                break
            await asyncio.sleep(0.05)
        assert shown['text'] == '知道，OpenCV' and shown['status'] == 'running'
        assert not worker.done() and not provider.release.is_set()
    finally:
        provider.release.set()
        await worker
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed' and final['text'] == '知道，OpenCV 是计算机视觉库。'
    assert provider.calls == 1 and provider.closed


async def test_quota_error_blocks_queued_searches_and_a_new_user_request_can_retry(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []
    quota = [True]

    def web(request: httpx.Request) -> httpx.Response:
        if quota[0]:
            return httpx.Response(403, json={'code': '403', 'msg': 'You do not have enough money or package quota'})
        return httpx.Response(200, json={'code': 200, 'data': {'webPages': {'value': []}}})

    app.state.container.work_agent.context.web_search = search_provider(calls, web)

    def response(body: dict[str, object]) -> dict[str, object]:
        assert body.get('tools'), 'A separate final rewrite must not run'
        messages = current_messages(body)
        returned = [message for message in messages if message['role'] == 'tool']
        if returned:
            if quota[0]:
                assert 'web_search' not in {tool['function']['name'] for tool in body['tools']}
                assert all(
                    json.loads(message['content'])['search_policy']['available'] is False for message in returned
                )
            return {
                'role': 'assistant',
                'content': '搜索额度不足，当前没有取得实时天气。' if quota[0] else '本轮已重新查询。',
            }
        first = WorkspaceProvider.tool('web_search', {'query': '贵阳天气'}, 'weather-one')
        if quota[0]:
            second = WorkspaceProvider.tool('web_search', {'query': '贵阳气象台'}, 'weather-two')
            first['tool_calls'].extend(second['tool_calls'])
        return first

    monkeypatch.setattr(workspace_provider, 'response', response)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id, '贵阳天气')
    first = await turn(admin, task, task['goal'])
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{first["id"]}')).json()
    assert final['status'] == 'completed' and '额度不足' in final['text'] and len(calls) == 1
    quota[0] = False
    task = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    retry = await turn(admin, task, '你再重新查一下')
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{retry["id"]}')).json()
    assert final['status'] == 'completed' and len(calls) == 2
    assert '重新查询' in final['text']


async def test_search_budget_is_enforced_before_excess_provider_requests(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []
    context = app.state.container.work_agent.context
    context.web_search = search_provider(calls)
    context.policy = replace(context.policy, web_search_calls=2)

    def response(body: dict[str, object]) -> dict[str, object]:
        assert body.get('tools')
        if 'web_search' in {tool['function']['name'] for tool in body['tools']}:
            return WorkspaceProvider.tool(
                'web_search', {'query': '查找投档信息 ' + str(len(calls))}, 'search-' + str(len(calls))
            )
        return {'role': 'assistant', 'content': '已完成本轮检索，具体院校投档分尚未核实。'}

    monkeypatch.setattr(workspace_provider, 'response', response)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id, '查询录取信息')
    run = await turn(admin, task, task['goal'])
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed' and len(calls) == 2
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert len([step for step in detail['steps'] if step['name'] == 'web_search']) == 2
