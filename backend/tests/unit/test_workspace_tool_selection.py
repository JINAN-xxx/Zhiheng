import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.messages import AIMessage

from tests.unit.test_workspace_tools import make_tools
from zhiheng.infrastructure.agents.adapters.workspace import _ExecutionContext, _ExecutionMiddleware, _tools
from zhiheng.modules.models.public import estimate_tool_schemas
from zhiheng.modules.work.public import WORK_TOOLSETS
from zhiheng.shared.errors import AppError


def selection(code: bool = True) -> _ExecutionMiddleware:
    context = _ExecutionContext(AsyncMock(), {}, execution_instructions='EXECUTION_ONLY_INSTRUCTIONS')
    registry = _tools(context, code, True)
    context.available_tools = (*registry, *TodoListMiddleware().tools)
    return _ExecutionMiddleware(context)


def test_initial_tool_schemas_are_smaller_and_groups_expose_only_their_own_actions() -> None:
    middleware = selection()
    initial = middleware.select_tools({})
    assert {tool.name for tool in initial} == {
        'list_inputs',
        'read_document',
        'read_sheet',
        'load_skill',
        'request_input',
        'write_todos',
        'web_search',
        'load_tools',
    }
    initial_tokens = estimate_tool_schemas(middleware.context.schemas)
    python = middleware.select_tools({'loaded_toolsets': {'python': True}})
    assert 'run_python' in {tool.name for tool in python} and 'execute' not in {tool.name for tool in python}
    assert 'EXECUTION_ONLY_INSTRUCTIONS' not in middleware._system({})
    assert 'EXECUTION_ONLY_INSTRUCTIONS' in middleware._system({'loaded_toolsets': {'python': True}})
    middleware.select_tools({'loaded_toolsets': dict.fromkeys(WORK_TOOLSETS, True)})
    assert initial_tokens < estimate_tool_schemas(middleware.context.schemas)


def test_code_groups_are_not_exposed_by_schema_or_stale_checkpoint_flags_without_permission() -> None:
    middleware = selection(False)
    visible = middleware.select_tools({'loaded_toolsets': dict.fromkeys(WORK_TOOLSETS, True)})
    assert not {'run_python', 'execute', 'write_file', 'publish_outputs'}.intersection(tool.name for tool in visible)
    loader = next(tool for tool in visible if tool.name == 'load_tools')
    schema = json.dumps(loader.args_schema.model_json_schema())
    assert 'inspection' in schema and 'python' not in schema and 'shell' not in schema


async def test_unloaded_tool_call_is_rejected_before_the_business_handler_runs() -> None:
    middleware = selection()
    request = SimpleNamespace(
        tool_call={'id': 'unexpected-python', 'name': 'run_python', 'args': {'code': 'print(1)'}},
        state={'messages': [AIMessage(id='model-1', content='')], 'toolsets_revision': 1},
    )
    handler = AsyncMock()
    result = await middleware.awrap_tool_call(request, handler)
    handler.assert_not_awaited()
    assert result.status == 'error' and json.loads(result.content)['error']['code'] == 'work_tool_not_loaded'


async def test_cached_code_group_loading_rechecks_current_permission_before_reusing_result() -> None:
    arguments = {'groups': ['python']}
    cached = SimpleNamespace(
        kind='tool',
        name='load_tools',
        status='succeeded',
        details=make_tools()._tool_details(arguments),
        result={'success': True, 'data': {'loaded_toolsets': ['python']}},
    )
    tools = make_tools(cached)

    async def guard(*args: object, code: bool = False) -> object:
        if code:
            raise AppError('forbidden', '执行权限已撤回', 403)
        return tools.principal

    tools.service.guard = AsyncMock(side_effect=guard)
    with pytest.raises(AppError, match='执行权限已撤回'):
        await tools.invoke('load_tools', arguments, 'cached-load')
    tools.context.tasks.cached_step.assert_not_awaited()
    assert tools.session is None


async def test_loading_records_a_real_step_without_starting_a_sandbox() -> None:
    tools = make_tools()
    result = await tools.invoke('load_tools', {'groups': ['python', 'inspection']}, 'load')
    assert result['data']['loaded_toolsets'] == ['inspection', 'python']
    assert 'run_python' in result['data']['tools'] and 'execute' not in result['data']['tools']
    assert tools.session is None and tools.progress.tool_calls == 1
    assert tools.context.tasks.record_step.await_args.kwargs['status'] == 'succeeded'
    tools.service.guard.assert_any_await(tools.claim, code=True)


@pytest.mark.parametrize('groups', [[], ['unknown'], ['python', {}], 'python'])
async def test_invalid_group_requests_do_not_enable_any_tools(groups: object) -> None:
    tools = make_tools()
    with pytest.raises(AppError) as error:
        await tools.invoke('load_tools', {'groups': groups}, 'invalid-group')
    assert error.value.code == 'invalid_work_toolset' and tools.session is None
