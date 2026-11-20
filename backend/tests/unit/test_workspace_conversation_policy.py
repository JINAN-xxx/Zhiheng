from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from tests.unit.test_workspace_tool_selection import selection
from tests.unit.test_workspace_tools import make_tools, workspace_state
from zhiheng.application.work.execution import ExecutionProgress, WorkAgentExecutor
from zhiheng.application.work.search_policy import SearchPolicy
from zhiheng.modules.search.public import WebSearchError, WebSearchResult
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


async def test_quota_failure_stops_parallel_followup_requests_and_hides_search_until_a_new_turn() -> None:
    provider = SimpleNamespace(
        search=AsyncMock(side_effect=WebSearchError('web_search_quota_exhausted', '搜索额度不足', attempted=True))
    )
    tools = make_tools()
    tools.context.web_search = provider
    first = await tools.invoke('web_search', {'query': '贵阳天气'}, 'first')
    second = await tools.invoke('web_search', {'query': '贵阳气象台预报'}, 'second')
    assert first['success'] is second['success'] is False and provider.search.await_count == 1
    assert not second['search_policy']['available']
    middleware = selection()
    middleware.context.tool_available = lambda name: name != 'web_search' or tools.search_policy.available
    assert 'web_search' not in {tool.name for tool in middleware.select_tools({})}
    provider.search.side_effect = None
    provider.search.return_value = WebSearchResult(query='贵阳天气', fetched_at=utcnow(), results=())
    retry = make_tools()
    retry.context.web_search = provider
    assert (await retry.invoke('web_search', {'query': '贵阳天气'}, 'next-turn'))['success']
    assert provider.search.await_count == 2


async def test_search_attempt_budget_limits_real_calls_even_if_the_model_keeps_asking() -> None:
    tools = make_tools()
    tools.search_policy = SearchPolicy(limit=2)
    provider = SimpleNamespace(
        search=AsyncMock(return_value=WebSearchResult(query='q', fetched_at=utcnow(), results=()))
    )
    tools.context.web_search = provider
    for number in range(2):
        assert (await tools.invoke('web_search', {'query': f'q{number}'}, f'query-{number}'))['success']
    final = await tools.invoke('web_search', {'query': 'another query'}, 'over-budget')
    assert final['error']['code'] == 'web_search_turn_limit' and provider.search.await_count == 2
    assert final['search_policy']['remaining_attempts'] == 0


async def test_uncertain_search_does_not_retry_an_unconfirmed_provider_request() -> None:
    tools = make_tools()
    provider = SimpleNamespace(
        search=AsyncMock(
            side_effect=WebSearchError(
                'web_search_uncertain',
                '原请求结果未确认',
                attempted=True,
                uncertain=True,
            )
        )
    )
    tools.context.web_search = provider
    with pytest.raises(AppError) as error:
        await tools.invoke('web_search', {'query': '贵阳天气'}, 'uncertain')
    assert error.value.code == 'work_execution_uncertain' and provider.search.await_count == 1
    assert tools.context.tasks.record_step.await_args.kwargs['status'] == 'unknown'


def test_recovered_search_budget_uses_its_run_and_does_not_double_count_blocked_steps() -> None:
    run_id = uuid4()
    policy = SearchPolicy()
    policy.restore(
        [
            SimpleNamespace(
                run_id=uuid4(),
                name='web_search',
                status='failed',
                result={'error': {'code': 'web_search_quota_exhausted'}},
            ),
            SimpleNamespace(
                run_id=run_id, name='web_search', status='succeeded', result={'search_policy': {'attempts': 2}}
            ),
            SimpleNamespace(
                run_id=run_id, name='web_search', status='failed', result={'search_policy': {'attempts': 2}}
            ),
        ],
        run_id,
    )
    assert policy.calls == 2 and policy.available


def test_completed_plan_does_not_force_another_model_review() -> None:
    middleware = selection()
    state = {
        'messages': [AIMessage(id='answer', content='已完成。')],
        'todos': [{'content': '核对资料', 'status': 'completed'}],
    }
    assert middleware.after_model(state, None) is None
    state['todos'][0]['status'] = 'pending'
    assert middleware.after_model(state, None)['jump_to'] == 'model'


async def test_empty_model_snapshot_does_not_erase_already_displayed_content() -> None:
    sessions = MagicMock()
    sessions.return_value.__aenter__.return_value = object()
    context = SimpleNamespace(
        database=SimpleNamespace(sessions=sessions),
        model_executor=None,
        runs=SimpleNamespace(snapshot=AsyncMock(return_value=SimpleNamespace(text='', status='completed'))),
    )
    executor = WorkAgentExecutor(SimpleNamespace(context=context))
    progress = ExecutionProgress(text='正在查询贵阳天气。')
    claim = SimpleNamespace(owner_id=uuid4())
    with patch.object(executor, '_project', AsyncMock()) as project:
        await executor._wait_model(claim, progress, SimpleNamespace(id=uuid4()), project_text=True)
    assert progress.text == '正在查询贵阳天气。'
    project.assert_not_awaited()


async def test_admin_followup_does_not_open_a_sandbox_for_previous_turn_outputs() -> None:
    tools = make_tools()
    tools.service.guard.return_value = Principal(tools.principal.id, 'admin', True)
    files = [{'path': 'outputs/report.txt', 'file_id': str(uuid4()), 'sha256': 'verified'}]
    tools.context.tasks.latest_workspace_files.return_value = workspace_state(3, files)
    with patch.object(tools, '_publish', AsyncMock()) as publish:
        await tools.finalize_outputs()
    publish.assert_not_awaited()
