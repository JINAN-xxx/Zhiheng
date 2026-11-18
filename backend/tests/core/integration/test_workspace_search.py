"""The search tool uses the existing native Agent, run, checkpoint and permission path."""

import json
from collections.abc import Callable
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import func, select

from tests.core.integration.test_platform_module_permissions import grant, member
from tests.core.integration.test_work_agent_execution import consume, turn, workspace_model
from tests.core.integration.test_work_agent_execution import (
    workspace_provider as workspace_provider,  # noqa: PLC0414 - register the shared pytest fixture
)
from tests.core.permission_support import grants
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.modules.search.adapters.bocha import BochaSearch
from zhiheng.modules.work.orm import WorkSandboxLease

SEARCH_KEY = 'fixture-search-credential'
SOURCE_URL = 'https://example.org/official-docs'


def provider_reply(body: dict[str, object]) -> dict[str, object]:
    if body.get('tools'):
        tools = body['tools']
        if not any(tool['function']['name'] == 'web_search' for tool in tools):
            return {'role': 'assistant', 'content': '当前没有联网工具。'}
        returned = [item for item in body['messages'] if item['role'] == 'tool']
        if not returned:
            return WorkspaceProvider.tool('web_search', {'query': '公开文档查询', 'count': 2}, 'search-once')
        result = json.loads(returned[-1]['content'])
        assert result['data']['results'][0]['url'] == SOURCE_URL
        return {'role': 'assistant', 'content': f'根据[官方文档]({SOURCE_URL})，已确认公开资料。'}
    facts = json.loads(body['messages'][-1]['content'])
    return {'role': 'assistant', 'content': facts['candidate']}


def search_provider(
    calls: list[httpx.Request], handler: Callable[[httpx.Request], httpx.Response] | None = None
) -> BochaSearch:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if handler is not None:
            return handler(request)
        return httpx.Response(
            200,
            json={
                'code': 200,
                'data': {
                    'webPages': {
                        'value': [
                            {
                                'name': '官方文档',
                                'url': SOURCE_URL,
                                'summary': '实际搜索结果',
                                'siteName': '示例站点',
                                'datePublished': '2026-11-18',
                            },
                        ]
                    }
                },
            },
        )

    return BochaSearch(SecretStr(SEARCH_KEY), transport=httpx.MockTransport(handle))


async def create_task(client: httpx.AsyncClient, model_id: str) -> dict[str, object]:
    result = await client.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': '搜索最新公开资料并给出来源',
        },
    )
    assert result.status_code == 201, result.text
    return result.json()


async def test_search_is_builtin_for_work_use_without_a_new_switch_or_sandbox(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []
    app.state.container.work_agent.context.web_search = search_provider(calls)
    monkeypatch.setattr(workspace_provider, 'response', provider_reply)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    await grant(app, *grants('platform:work', 'create', 'use'))
    async with member(app) as client:
        task = await create_task(client, model_id)
        assert set(task['configuration']) == {'model_id', 'skill_slugs'}
        run = await turn(client, task, task['goal'], initial=True)
        await consume(app)
        final = (await client.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
        assert final['status'] == 'completed', final
        assert SOURCE_URL in final['text']
        detail = (await client.get(f'/api/work/tasks/{task["id"]}')).json()
        searches = [item for item in detail['steps'] if item['name'] == 'web_search']
        assert len(calls) == len(searches) == 1
        assert searches[0]['status'] == 'succeeded'
        assert searches[0]['result']['data']['search_requests'] == 1
        assert searches[0]['result']['data']['results'][0]['url'] == SOURCE_URL
        assert SEARCH_KEY not in json.dumps(detail) and SEARCH_KEY not in json.dumps(workspace_provider.requests)
        assert all(
            'run_python' not in [tool['function']['name'] for tool in request.get('tools', [])]
            for request in workspace_provider.requests
        )
        assert (await client.get('/api/models/connections')).status_code == 403
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WorkSandboxLease)) == 0


async def test_search_does_not_register_without_a_configured_key(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.container.work_agent.context.web_search = BochaSearch(SecretStr(''))
    monkeypatch.setattr(workspace_provider, 'response', provider_reply)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id)
    run = await turn(admin, task, task['goal'], initial=True)
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed' and SOURCE_URL not in final['text']
    assert all(
        'web_search' not in [tool['function']['name'] for tool in request.get('tools', [])]
        for request in workspace_provider.requests
    )


async def test_search_configuration_change_preserves_the_tools_of_an_already_admitted_run(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = app.state.container.work_agent.context
    context.web_search = None
    monkeypatch.setattr(workspace_provider, 'response', provider_reply)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id)
    run = await turn(admin, task, task['goal'], initial=True)
    calls: list[httpx.Request] = []
    context.web_search = search_provider(calls)
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed' and SOURCE_URL not in final['text']
    assert not calls
    assert all(
        'web_search' not in [tool['function']['name'] for tool in request.get('tools', [])]
        for request in workspace_provider.requests
    )


async def test_existing_work_use_permission_still_prevents_unauthorized_search(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
) -> None:
    calls: list[httpx.Request] = []
    app.state.container.work_agent.context.web_search = search_provider(calls)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    role_id = await grant(app, *grants('platform:work', 'create', 'use'))
    async with member(app) as client:
        task = await create_task(client, model_id)
        roles = (await admin.get(f'/api/admin/roles/{role_id}')).json()
        changed = await admin.patch(
            f'/api/admin/roles/{role_id}',
            json={
                'permissions': sorted(grants('platform:work', 'read')),
                'expected_version': roles['version'],
            },
        )
        assert changed.status_code == 200, changed.text
        result = await client.post(
            f'/api/work/tasks/{task["id"]}/runs',
            json={
                'client_request_id': task['create_client_request_id'],
                'expected_version': task['version'],
                'message': task['goal'],
            },
        )
        assert result.status_code == 403
    assert not calls


async def test_confirmed_search_result_replays_without_a_second_http_request_after_checkpoint_failure(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []
    container = app.state.container
    container.work_agent.context.web_search = search_provider(calls)
    monkeypatch.setattr(workspace_provider, 'response', provider_reply)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id)
    save = container.work_agent_store.save_execution_memory
    failures = 0

    async def interrupted(*args, **kwargs) -> None:
        nonlocal failures
        if failures == 0:
            failures += 1
            raise RuntimeError('test checkpoint acknowledgement failure')
        return await save(*args, **kwargs)

    monkeypatch.setattr(container.work_agent_store, 'save_execution_memory', interrupted)
    first = await turn(admin, task, task['goal'], initial=True)
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{first["id"]}')).json()
    assert final['status'] == 'failed' and len(calls) == 1
    task = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    resumed = await admin.post(
        f'/api/work/tasks/{task["id"]}/runs',
        json={
            'client_request_id': str(uuid4()),
            'expected_version': task['version'],
            'resume': True,
        },
    )
    assert resumed.status_code == 202, resumed.text
    await consume(app)
    current = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed.json()["id"]}')).json()
    assert current['status'] == 'completed', current
    assert len(calls) == 1 and SOURCE_URL in current['text']


async def test_quota_rejection_uses_the_existing_tool_error_path_without_fake_sources(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[httpx.Request] = []
    app.state.container.work_agent.context.web_search = search_provider(
        calls,
        lambda _: httpx.Response(
            403,
            json={
                'code': '403',
                'message': 'You do not have enough money or package quota',
            },
        ),
    )

    def reply(body: dict[str, object]) -> dict[str, object]:
        returned = [item for item in body['messages'] if item['role'] == 'tool']
        if body.get('tools') and not returned:
            return WorkspaceProvider.tool('web_search', {'query': '公开文档查询'}, 'search-once')
        if returned:
            result = json.loads(returned[-1]['content'])
            assert result['success'] is False and result['error']['code'] == 'web_search_quota_exhausted'
        return {'role': 'assistant', 'content': '博查搜索额度不足，暂时无法查询最新网页。'}

    monkeypatch.setattr(workspace_provider, 'response', reply)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id)
    run = await turn(admin, task, task['goal'], initial=True)
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed' and '额度不足' in final['text']
    assert SOURCE_URL not in final['text'] and len(calls) == 1
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    search = next(step for step in detail['steps'] if step['name'] == 'web_search')
    assert search['status'] == 'failed' and search['result']['error']['code'] == 'web_search_quota_exhausted'
