"""Summary budgets, failure preservation and trusted history recovery."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from langgraph.types import StateSnapshot

from zhiheng.application.work.context import WorkspacePolicy
from zhiheng.application.work.execution import ExecutionProgress, WorkAgentExecutor
from zhiheng.infrastructure.agents.adapters.workspace import _restore_summary_history, _summary_middleware, _tokens
from zhiheng.modules.models.public import AgentModelMessage, ModelBinding
from zhiheng.modules.work.public import ModelTurn, valid_work_summary
from zhiheng.runtime.contracts import ClaimedRun
from zhiheng.shared.errors import AppError


def pair(number: int, content: str) -> list[BaseMessage]:
    return [
        AIMessage(
            id=f'ai-{number}',
            content='',
            tool_calls=[{'id': f'call-{number}', 'name': 'read_document', 'args': {'start': number}}],
        ),
        ToolMessage(id=f'tool-{number}', content=content, tool_call_id=f'call-{number}'),
    ]


def history() -> list[BaseMessage]:
    return [
        HumanMessage(id='question', content='阅读报告并保留三个部分的结论。'),
        *pair(1, 'REPORT_HEAD ' + '甲' * 3000),
        *pair(2, 'REPORT_TAIL ' + '乙' * 3000),
    ]


async def test_large_tool_history_reaches_the_real_summary_call_without_the_sdk_four_thousand_token_trim() -> None:
    calls: list[tuple[AgentModelMessage, ...]] = []

    async def invoke(
        messages: tuple[AgentModelMessage, ...], _schemas: tuple[dict[str, object], ...], _key: str, purpose: str
    ) -> ModelTurn:
        assert purpose == 'summary'
        calls.append(messages)
        return ModelTurn(uuid4(), '已读报告前段 REPORT_HEAD，接下来结合 REPORT_TAIL 回答。')

    messages = history()
    before = [message.model_dump() for message in messages]
    middleware = _summary_middleware(invoke, 8000, 2000)
    result = await middleware.abefore_model({'messages': messages}, Runtime())
    assert len(calls) == 1 and _tokens(messages) > 8000
    prompt = '\n'.join(message.content for message in calls[0])
    assert 'REPORT_HEAD' in prompt and '阅读报告并保留三个部分的结论。' in prompt
    assert result is not None
    text = '\n'.join(str(message.content) for message in result['messages'])
    assert 'REPORT_HEAD' in text and 'REPORT_TAIL' in text
    assert 'too long to summarize' not in text
    assert [message.model_dump() for message in messages] == before


@pytest.mark.parametrize(
    'response', ['', 'Previous conversation was too long to summarize.', 'No previous conversation history.']
)
async def test_invalid_summary_cannot_replace_original_messages(response: str) -> None:
    async def invoke(
        _messages: tuple[AgentModelMessage, ...], _schemas: tuple[dict[str, object], ...], _key: str, _purpose: str
    ) -> ModelTurn:
        return ModelTurn(uuid4(), response)

    messages = history()
    before = [message.model_dump() for message in messages]
    with pytest.raises(AppError) as caught:
        await _summary_middleware(invoke, 8000, 2000).abefore_model({'messages': messages}, Runtime())
    assert caught.value.code == 'work_summary_failed'
    assert [message.model_dump() for message in messages] == before


async def test_summary_provider_failure_preserves_original_messages() -> None:
    async def invoke(
        _messages: tuple[AgentModelMessage, ...], _schemas: tuple[dict[str, object], ...], _key: str, _purpose: str
    ) -> ModelTurn:
        raise AppError('model_idle_timeout', '模拟上游停流', 504)

    messages = history()
    before = [message.model_dump() for message in messages]
    with pytest.raises(AppError, match='模拟上游停流'):
        await _summary_middleware(invoke, 8000, 2000).abefore_model({'messages': messages}, Runtime())
    assert [message.model_dump() for message in messages] == before


def snapshot(messages: list[BaseMessage], identity: str, parent: str | None, thread: str = 'task-1') -> StateSnapshot:
    config: RunnableConfig = {'configurable': {'thread_id': thread, 'checkpoint_ns': '', 'checkpoint_id': identity}}
    parent_config: RunnableConfig | None = (
        {'configurable': {'thread_id': thread, 'checkpoint_ns': '', 'checkpoint_id': parent}} if parent else None
    )
    return StateSnapshot(
        values={'messages': messages},
        next=('model',),
        config=config,
        metadata=None,
        created_at=None,
        parent_config=parent_config,
        tasks=(),
        interrupts=(),
    )


class HistoryGraph:
    def __init__(self, states: dict[str, StateSnapshot]) -> None:
        self.states = states
        self.reads: list[str] = []

    async def aget_state(self, config: RunnableConfig) -> StateSnapshot:
        identity = str(config['configurable']['checkpoint_id'])
        self.reads.append(identity)
        return self.states[identity]


def broken(identity: str) -> HumanMessage:
    return HumanMessage(
        id=identity,
        content='Here is a summary of the conversation to date:\\n\\nPrevious conversation was too long to summarize.',
        additional_kwargs={'lc_source': 'summarization'},
    )


async def test_legacy_bad_summaries_restore_all_intermediate_messages_from_trusted_ancestors_only() -> None:
    first = snapshot([HumanMessage(id='question', content='阅读完整报告'), *pair(1, 'REPORT_HEAD')], 'good', None)
    middle = snapshot([broken('bad-1'), *pair(2, 'REPORT_MIDDLE')], 'middle', 'good')
    latest = snapshot([broken('bad-2'), *pair(3, 'REPORT_TAIL')], 'latest', 'middle')
    graph = HistoryGraph(
        {'good': first, 'middle': middle, 'foreign': snapshot([HumanMessage(content='foreign data')], 'foreign', None)}
    )
    restored = await _restore_summary_history(graph, latest.config, latest)
    assert restored is not None
    assert [message.id for message in restored] == ['question', 'ai-1', 'tool-1', 'ai-2', 'tool-2', 'ai-3', 'tool-3']
    assert graph.reads == ['middle', 'good']
    assert sum(isinstance(message, ToolMessage) for message in restored) == 3
    assert latest.values['messages'][0].id == 'bad-2'


async def test_recovery_refuses_foreign_thread_or_missing_parent_without_replacing_messages() -> None:
    latest = snapshot([broken('bad')], 'latest', 'foreign', 'other-task')
    config: RunnableConfig = {'configurable': {'thread_id': 'task-1', 'checkpoint_id': 'latest'}}
    graph = HistoryGraph({})
    with pytest.raises(AppError) as caught:
        await _restore_summary_history(graph, config, latest)
    assert caught.value.code == 'work_summary_context_unavailable' and not graph.reads


async def test_user_quoted_placeholder_is_not_treated_as_an_sdk_summary() -> None:
    value = snapshot(
        [HumanMessage(id='question', content='Previous conversation was too long to summarize.')], 'latest', None
    )
    assert await _restore_summary_history(HistoryGraph({}), value.config, value) is None


def test_explicit_summary_input_budget_is_enforced_before_model_admission() -> None:
    executor = WorkAgentExecutor(SimpleNamespace(context=SimpleNamespace(policy=WorkspacePolicy())))
    binding = ModelBinding(model_id=uuid4(), connection_id=uuid4(), upstream_model='fixture', max_output_tokens=8192)
    claim = ClaimedRun(uuid4(), 'work_agent', uuid4(), uuid4(), uuid4(), {'binding': binding.model_dump(mode='json')})
    progress = ExecutionProgress(summary_input_tokens=400, summary_output_tokens=128)
    with pytest.raises(AppError) as caught:
        executor._model_request(claim, progress, (AgentModelMessage(role='user', content='甲' * 300),), (), 'summary')
    assert caught.value.code == 'work_summary_too_large'
    progress.summary_input_tokens = 4000
    assert (
        executor._model_request(
            claim, progress, (AgentModelMessage(role='user', content='甲' * 300),), (), 'summary'
        ).binding.max_output_tokens
        == 128
    )


@pytest.mark.parametrize(
    'text',
    [
        '',
        'Previous conversation was too long to summarize.',
        'Here is a summary of the conversation to date:\\n\\nNo previous conversation history.',
    ],
)
def test_placeholder_summaries_are_invalid(text: str) -> None:
    assert not valid_work_summary(text)
