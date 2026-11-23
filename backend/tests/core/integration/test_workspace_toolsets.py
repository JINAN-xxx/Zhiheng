"""Dynamic tool schemas over native execution, authorization and real checkpoints."""

from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select

from tests.core.integration.test_platform_module_permissions import grant, member
from tests.core.integration.test_work_agent_execution import consume, turn, workspace_model
from tests.core.integration.test_work_agent_execution import workspace_provider as workspace_provider  # noqa: PLC0414
from tests.core.integration.test_work_agent_resume import continue_task
from tests.core.permission_support import grants
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.application.work.tools import WorkspaceTools
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.agents.adapters.workspace import (
    WorkspaceKernel,
    WorkspaceState,
    _ExecutionContext,
    _ExecutionMiddleware,
    _summary_middleware,
    _tools,
    _WorkspaceModel,
)
from zhiheng.modules.models.public import AgentModelMessage
from zhiheng.modules.work.orm import WorkSandboxLease
from zhiheng.modules.work.public import ModelTurn
from zhiheng.shared.errors import AppError


async def create_task(client: httpx.AsyncClient, model_id: str, goal: str) -> dict[str, object]:
    response = await client.post(
        '/api/work/tasks',
        json={
            'client_request_id': str(uuid4()),
            'model_id': model_id,
            'goal': goal,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def names(body: dict[str, object]) -> set[str]:
    return {tool['function']['name'] for tool in body.get('tools', [])}


async def test_loaded_groups_survive_resume_and_new_messages_in_the_same_task(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[set[str]] = []
    finalizations = 0
    finalize = WorkspaceTools.finalize_outputs

    async def finish(self: WorkspaceTools) -> None:
        nonlocal finalizations
        await finalize(self)
        finalizations += 1
        if finalizations == 1:
            raise AppError('work_artifact_invalid', 'test finalization failure', 422)

    def response(body: dict[str, object]) -> dict[str, object]:
        if not body.get('tools'):
            raise AssertionError('An extra delivery model must not run')
        visible = names(body)
        seen.append(visible)
        latest = next(item['content'] for item in reversed(body['messages']) if item['role'] == 'user')
        if latest == 'FOLLOWUP: explain without processing files':
            assert 'run_python' in visible and 'execute' not in visible
            return {'role': 'assistant', 'content': '直接回答新的问题。'}
        if 'run_python' not in visible:
            return WorkspaceProvider.tool('load_tools', {'groups': ['python']}, 'load-python')
        assert 'execute' not in visible and 'publish_outputs' in visible
        return {'role': 'assistant', 'content': '工具已就绪，本轮不执行代码。'}

    monkeypatch.setattr(workspace_provider, 'response', response)
    monkeypatch.setattr(WorkspaceTools, 'finalize_outputs', finish)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id, 'Prepare Python tools')
    first = await turn(admin, task, task['goal'])
    await consume(app)
    assert (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{first["id"]}')).json()['status'] == 'failed'
    resumed = await continue_task(admin, task['id'])
    await consume(app)
    assert (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{resumed["id"]}')).json()['status'] == 'completed'
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    followup = await turn(admin, detail, 'FOLLOWUP: explain without processing files')
    await consume(app)
    assert (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{followup["id"]}')).json()['status'] == 'completed'
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    loads = [step for step in detail['steps'] if step['name'] == 'load_tools']
    assert len(loads) == 1 and loads[0]['status'] == 'succeeded'
    assert 'run_python' not in seen[0] and 'run_python' in seen[1] and 'run_python' in seen[2]
    assert 'run_python' in seen[-1]
    async with app.state.container.database.sessions() as session:
        assert not list(await session.scalars(select(WorkSandboxLease)))


async def test_unavailable_model_tool_is_rejected_and_model_can_correct_once(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def response(body: dict[str, object]) -> dict[str, object]:
        visible = names(body)
        system = next(item['content'] for item in body['messages'] if item['role'] == 'system')
        if 'run_python' in visible:
            return {'role': 'assistant', 'content': '工具已就绪。'}
        if '上次请求的工具不在本轮工具列表中' in system:
            return WorkspaceProvider.tool('load_tools', {'groups': ['python']}, 'load-after-correction')
        return WorkspaceProvider.tool('run_python', {'code': 'print(1)'}, 'unavailable-python')

    monkeypatch.setattr(workspace_provider, 'response', response)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id, 'Prepare a document')
    run = await turn(admin, task, task['goal'])
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed', final
    assert final['usage'] == {'input_tokens': 51, 'output_tokens': 9, 'status': 'known'}
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert [step['name'] for step in detail['steps'] if step['kind'] == 'tool'] == ['load_tools']
    assert len(workspace_provider.requests) == 3
    async with app.state.container.database.sessions() as session:
        assert not list(await session.scalars(select(WorkSandboxLease)))


async def test_parallel_group_loads_merge_without_enabling_unrequested_shell_tools(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[set[str]] = []

    def response(body: dict[str, object]) -> dict[str, object]:
        if not body.get('tools'):
            return {'role': 'assistant', 'content': '工具组已核对。'}
        visible = names(body)
        seen.append(visible)
        if 'run_python' not in visible:
            python = WorkspaceProvider.tool('load_tools', {'groups': ['python']}, 'load-python')
            inspection = WorkspaceProvider.tool('load_tools', {'groups': ['inspection']}, 'load-inspection')
            python['tool_calls'].extend(inspection['tool_calls'])
            return python
        assert {'run_python', 'inspect_file', 'read_history', 'publish_outputs'} <= visible
        assert 'execute' not in visible
        return {'role': 'assistant', 'content': '所需工具已准备好。'}

    monkeypatch.setattr(workspace_provider, 'response', response)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    task = await create_task(admin, model_id, 'Prepare analysis and inspection')
    run = await turn(admin, task, task['goal'])
    await consume(app)
    final = (await admin.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
    assert final['status'] == 'completed', final
    detail = (await admin.get(f'/api/work/tasks/{task["id"]}')).json()
    assert (
        len([step for step in detail['steps'] if step['name'] == 'load_tools' and step['status'] == 'succeeded']) == 2
    )
    assert len(seen) == 2


async def test_forged_code_group_cannot_give_a_regular_user_execution_tools(
    admin: httpx.AsyncClient,
    app: FastAPI,
    workspace_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested = False

    def response(body: dict[str, object]) -> dict[str, object]:
        nonlocal requested
        assert not {'run_python', 'execute', 'write_file'}.intersection(names(body))
        if body.get('tools') and not requested:
            requested = True
            return WorkspaceProvider.tool('load_tools', {'groups': ['python']}, 'forged-group')
        return {'role': 'assistant', 'content': '当前没有代码执行权限。'}

    monkeypatch.setattr(workspace_provider, 'response', response)
    model_id = await workspace_model(admin, workspace_provider, 'workspace-greeting')
    await grant(app, *grants('platform:work', 'create', 'use', 'update'))
    async with member(app) as client:
        task = await create_task(client, model_id, 'Try loading a code group')
        run = await turn(client, task, task['goal'])
        await consume(app)
        final = (await client.get(f'/api/work/tasks/{task["id"]}/runs/{run["id"]}')).json()
        assert final['status'] in {'completed', 'failed'}, final
        detail = (await client.get(f'/api/work/tasks/{task["id"]}')).json()
        assert not [step for step in detail['steps'] if step['name'] in {'run_python', 'execute', 'write_file'}]
        assert not [step for step in detail['steps'] if step['name'] == 'load_tools' and step['status'] == 'succeeded']
    async with app.state.container.database.sessions() as session:
        assert not list(await session.scalars(select(WorkSandboxLease)))


@pytest.mark.parametrize('legacy', [True, False])
async def test_only_trusted_legacy_pending_calls_can_resume_without_loading_their_group(
    app: FastAPI,
    settings: Settings,
    legacy: bool,
) -> None:
    del app  # Its fixture owns the migrated checkpoint schema and isolated database.
    kernel = WorkspaceKernel(settings.database_url.get_secret_value())
    task_id = uuid4()
    invoked: list[str] = []
    snapshots: list[str] = []

    async def tool(name: str, arguments: dict[str, object], key: str) -> dict[str, object]:
        invoked.append(name)
        return {'success': True, 'data': {'content': 'saved workspace text'}}

    async def model(
        messages: tuple[AgentModelMessage, ...], schemas: tuple[dict[str, object], ...], key: str, purpose: str
    ) -> ModelTurn:
        visible = {schema['function']['name'] for schema in schemas}
        assert ('read_file' in visible) is legacy
        assert 'execute' not in visible and 'run_python' not in visible
        if not legacy:
            assert any('work_tool_not_loaded' in message.content for message in messages)
        return ModelTurn(uuid4(), '已核对保存的执行状态。')

    async def noop(*args: object) -> None:
        return None

    async def checkpoint(value: str) -> None:
        snapshots.append(value)

    seed_context = _ExecutionContext(tool, {})
    async with kernel.checkpoints.saver() as saver:
        graph = create_agent(
            _WorkspaceModel(model),
            tools=_tools(seed_context, True),
            state_schema=WorkspaceState,
            middleware=[
                _summary_middleware(model, 16000, 5000),
                TodoListMiddleware(),
                _ExecutionMiddleware(seed_context),
            ],
            checkpointer=saver,
        )
        state = {
            'messages': [
                HumanMessage(id='user', content='Read the saved text'),
                AIMessage(
                    id='saved-model',
                    content='',
                    tool_calls=[
                        {
                            'id': 'pending-read',
                            'name': 'read_file',
                            'args': {'path': 'working/saved.txt'},
                        }
                    ],
                ),
            ],
            'todos': [],
        }
        if not legacy:
            state['toolsets_revision'] = 1
        seeded = await graph.aupdate_state(kernel.checkpoints.config(task_id), state, as_node='model')
        saved = await graph.aget_state(seeded)
        assert saved.next and (saved.values.get('toolsets_revision') != 1) is legacy
    outcome = await kernel.run(
        task_id=task_id,
        checkpoint_id=kernel.checkpoints.result_checkpoint_id(seeded),
        message=None,
        message_id=uuid4(),
        system_prompt='Use the visible tools only.',
        skill_texts={},
        code_enabled=True,
        invoke_model=model,
        invoke_tool=tool,
        write_progress=noop,
        write_checkpoint=checkpoint,
        input_budget=None,
        trigger_tokens=16000,
        keep_tokens=5000,
    )
    assert outcome.text == '已核对保存的执行状态。' and snapshots
    assert invoked == (['read_file'] if legacy else [])
