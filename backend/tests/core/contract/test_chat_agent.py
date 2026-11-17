"""Real Agent + local HTTP contracts; no external model or production database."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from copy import deepcopy
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from tests.core.provider import ProviderServer
from zhiheng.infrastructure.agents.adapters.agent import ROOT_CHECKPOINT_ID, AgentProvider
from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
from zhiheng.modules.models.contracts import ModelDelta, ModelImage, ModelMessage, ProviderError
from zhiheng.shared.errors import AppError


async def unused_summary(_messages: tuple[ModelMessage, ...]) -> str:
    raise AssertionError('Summary was not expected')


async def started() -> None:
    pass


def agent_provider(  # noqa: PLR0913 - exercise the adapter's explicit execution configuration
    server: ProviderServer,
    *,
    checkpointer: InMemorySaver | None = None,
    base_checkpoint_id: str | None = ROOT_CHECKPOINT_ID,
    message_ids: tuple[str, ...] = ('system', 'question'),
    incremental: bool = False,
    model: str = 'reasoning',
    trigger_tokens: int = 10000,
    keep_tokens: int = 1000,
    summarizer: Callable[[tuple[ModelMessage, ...]], Awaitable[str]] = unused_summary,
    on_model_start: Callable[[], Awaitable[None]] = started,
    input_budget: int | None = None,
    options: dict[str, object] | None = None,
) -> AgentProvider:
    raw = OpenAIChatProvider(
        base_url=f'http://127.0.0.1:{server.server_port}/v1',
        api_key='local-test-key',
        model=model,
        max_output_tokens=64,
        timeout_seconds=2,
        options={'temperature': 0.25, **(options or {})},
    )
    return AgentProvider.from_provider(
        raw,
        checkpointer=checkpointer or InMemorySaver(),
        thread_id='contract-conversation',
        base_checkpoint_id=base_checkpoint_id,
        message_ids=message_ids,
        incremental=incremental,
        trigger_tokens=trigger_tokens,
        keep_tokens=keep_tokens,
        summary_output_tokens=64,
        summarizer=summarizer,
        on_model_start=on_model_start,
        input_budget=input_budget,
    )


@pytest.mark.parametrize('json_mode', [False, True])
async def test_agent_keeps_one_http_call_reasoning_raw_usage_and_checkpoint(
    provider: ProviderServer,
    json_mode: bool,
) -> None:
    calls = []

    async def on_start() -> None:
        calls.append('model')

    model = agent_provider(
        provider, on_model_start=on_start, options={'response_format': 'json_object'} if json_mode else None
    )
    result = [delta async for delta in model.stream((ModelMessage('system', '平台规则'), ModelMessage('user', '你好')))]
    assert ''.join(delta.text for delta in result) == '你好'
    assert ''.join(delta.reasoning for delta in result) == '先思考'
    assert [(delta.input_tokens, delta.output_tokens) for delta in result if delta.input_tokens is not None] == [(8, 2)]
    assert result[-1].result == {
        'checkpoint_id': model.completed_checkpoint_id,
        'summary_count': 0,
        'summary_estimated_tokens': 0,
        'context_reservation_tokens': 44,
        'context_image_bytes': 0,
        'context_model_image_bytes': 0,
        'context_model_checkpoint_id': model.model_checkpoint_id,
        'context_model_reservation_tokens': 50,
        'context_estimated_tokens': 40,
        'context_model_input_tokens': 44,
        'context_retained_rounds': 1,
    }
    assert model.model_completed and model.completed_checkpoint_id
    assert calls == ['model']
    assert len(provider.requests) == 1
    assert provider.requests[0]['temperature'] == 0.25
    assert provider.requests[0]['stream_options'] == {'include_usage': True}
    assert 'tools' not in provider.requests[0]
    if json_mode:
        assert provider.requests[0]['response_format'] == {'type': 'json_object'}
    else:
        assert 'response_format' not in provider.requests[0]


async def test_next_turn_appends_only_new_user_and_refreshes_platform_prefix(provider: ProviderServer) -> None:
    saver = InMemorySaver()
    first = agent_provider(provider, checkpointer=saver)
    _ = [delta async for delta in first.stream((ModelMessage('system', '旧规则'), ModelMessage('user', '第一问')))]
    second = agent_provider(
        provider,
        checkpointer=saver,
        base_checkpoint_id=first.completed_checkpoint_id,
        message_ids=('new-system', 'question', 'answer', 'second-question'),
        incremental=True,
    )
    _ = [
        delta
        async for delta in second.stream(
            (
                ModelMessage('system', '新规则'),
                ModelMessage('user', '第一问'),
                ModelMessage('assistant', '你好'),
                ModelMessage('user', '第二问'),
            )
        )
    ]
    assert provider.requests[1]['messages'] == [
        {'content': '新规则', 'role': 'system'},
        {'content': '第一问', 'role': 'user'},
        {'content': '你好', 'role': 'assistant'},
        {'content': '第二问', 'role': 'user'},
    ]
    assert len(provider.requests) == 2
    state = await saver.aget_tuple({'configurable': {'thread_id': 'contract-conversation'}})
    assert state is not None
    assert all(message.type != 'system' for message in state.checkpoint['channel_values']['messages'])


async def test_full_frozen_seed_replaces_branch_input_without_duplicating_history(provider: ProviderServer) -> None:
    saver = InMemorySaver()
    first = agent_provider(provider, checkpointer=saver)
    _ = [delta async for delta in first.stream((ModelMessage('system', '规则'), ModelMessage('user', '旧问题')))]
    regenerated = agent_provider(
        provider,
        checkpointer=saver,
        base_checkpoint_id=first.completed_checkpoint_id,
        message_ids=('new-system', 'different-question'),
        incremental=False,
    )
    _ = [
        delta
        async for delta in regenerated.stream((ModelMessage('system', '新规则'), ModelMessage('user', '冻结问题')))
    ]
    assert provider.requests[1]['messages'] == [
        {'content': '新规则', 'role': 'system'},
        {'content': '冻结问题', 'role': 'user'},
    ]
    old = await saver.aget_tuple(
        {'configurable': {'thread_id': 'contract-conversation', 'checkpoint_id': first.completed_checkpoint_id}}
    )
    assert old is not None
    assert old.checkpoint['channel_values']['messages'][0].content == '旧问题'


async def test_summary_reads_earliest_facts_and_finishes_before_main_provider_boundary(
    provider: ProviderServer,
) -> None:
    summaries: list[tuple[ModelMessage, ...]] = []
    order = []

    async def summarize(messages: tuple[ModelMessage, ...]) -> str:
        summaries.append(messages)
        order.append('summary')
        return '保留最早事实和任务目标'

    async def on_start() -> None:
        order.append('main')

    model = agent_provider(
        provider,
        trigger_tokens=128,
        keep_tokens=32,
        message_ids=('system', 'old-user', 'old-answer', 'question'),
        summarizer=summarize,
        on_model_start=on_start,
    )
    oldest = 'EARLIEST-CRITICAL-FACT ' + 'history ' * 6000
    result = [
        delta
        async for delta in model.stream(
            (
                ModelMessage('system', '不可覆盖的平台规则'),
                ModelMessage('user', oldest),
                ModelMessage('assistant', '已确认最早事实'),
                ModelMessage('user', '继续'),
            )
        )
    ]
    assert len(summaries) == 1
    assert 'EARLIEST-CRITICAL-FACT' in summaries[0][1].content
    assert oldest in summaries[0][1].content
    assert '64 Token' in summaries[0][0].content
    assert order == ['summary', 'main']
    assert len(provider.requests) == 1
    assert '保留最早事实' in str(provider.requests[0]['messages'])
    assert 'EARLIEST-CRITICAL-FACT' not in str(provider.requests[0]['messages'])
    assert ''.join(delta.text for delta in result) == '你好'
    assert model.summary_count == 1
    assert result[-1].result['summary_estimated_tokens'] > 0
    assert 0 < result[-1].result['context_reservation_tokens'] < 1000
    assert 0 < result[-1].result['context_estimated_tokens'] < 1000
    assert 0 < result[-1].result['context_model_input_tokens'] < 1000
    assert result[-1].result['context_retained_rounds'] == 1


async def test_custom_summary_failure_has_no_hidden_retry_or_main_provider_start(provider: ProviderServer) -> None:
    calls = []

    async def summarize(_messages: tuple[ModelMessage, ...]) -> str:
        calls.append('summary')
        raise RuntimeError('summary service unavailable')

    async def on_start() -> None:
        calls.append('main')

    model = agent_provider(
        provider,
        trigger_tokens=32,
        keep_tokens=8,
        message_ids=('system', 'old-user', 'old-answer', 'question'),
        summarizer=summarize,
        on_model_start=on_start,
    )
    with pytest.raises(RuntimeError, match='summary service unavailable'):
        _ = [
            delta
            async for delta in model.stream(
                (
                    ModelMessage('system', '规则'),
                    ModelMessage('user', 'old history ' * 100),
                    ModelMessage('assistant', 'old answer ' * 100),
                    ModelMessage('user', '继续'),
                )
            )
        ]
    assert calls == ['summary']
    assert provider.requests == []
    assert not model.model_completed
    assert model.completed_checkpoint_id is None


class FailingSaver(InMemorySaver):
    def __init__(self, *, after_model: bool) -> None:
        super().__init__()
        self.after_model = after_model

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        messages = checkpoint['channel_values'].get('messages', [])
        completed = any(isinstance(message, AIMessage) for message in messages)
        if completed == self.after_model:
            raise OSError('checkpoint storage unavailable')
        return await super().aput(config, checkpoint, metadata, new_versions)


@pytest.mark.parametrize('after_model', [False, True])
async def test_checkpoint_failure_preserves_call_stage_and_known_usage(
    provider: ProviderServer, after_model: bool
) -> None:
    calls = []

    async def on_start() -> None:
        calls.append('main')

    model = agent_provider(
        provider,
        checkpointer=FailingSaver(after_model=after_model),
        on_model_start=on_start,
    )
    result: list[ModelDelta] = []
    with pytest.raises(OSError, match='checkpoint storage unavailable'):
        async for delta in model.stream((ModelMessage('system', '规则'), ModelMessage('user', '问题'))):
            result.append(delta)  # noqa: PERF401 - retain deltas emitted before the saver raises
    assert model.completed_checkpoint_id is None
    if after_model:
        assert len(provider.requests) == 1
        assert calls == ['main']
        assert model.model_completed
        assert ''.join(delta.text for delta in result) == '你好'
        assert [(d.input_tokens, d.output_tokens) for d in result if d.input_tokens is not None] == [(8, 2)]
    else:
        assert provider.requests == []
        assert calls == []
        assert not model.model_completed
        assert result == []


async def test_agent_does_not_synthesize_missing_usage(provider: ProviderServer) -> None:
    model = agent_provider(provider, model='no-usage')
    result = [delta async for delta in model.stream((ModelMessage('system', '规则'), ModelMessage('user', '问题')))]
    assert ''.join(delta.text for delta in result) == '你好'
    assert all(delta.input_tokens is delta.output_tokens is None for delta in result)
    assert len(provider.requests) == 1


async def test_agent_rejected_provider_has_no_retry(provider: ProviderServer) -> None:
    model = agent_provider(provider, model='rejected')
    with pytest.raises(ProviderError) as caught:
        _ = [delta async for delta in model.stream((ModelMessage('system', '规则'), ModelMessage('user', '问题')))]
    assert caught.value.definitive_rejection
    assert len(provider.requests) == 1
    assert not model.model_completed


async def test_missing_accepted_checkpoint_does_not_silently_start_incremental_chat(
    provider: ProviderServer,
) -> None:
    calls = []

    async def on_start() -> None:
        calls.append('main')

    model = agent_provider(
        provider,
        checkpointer=InMemorySaver(),
        base_checkpoint_id='missing-accepted-head',
        incremental=True,
        on_model_start=on_start,
    )
    with pytest.raises(AppError) as missing:
        _ = [delta async for delta in model.stream((ModelMessage('system', '规则'), ModelMessage('user', '问题')))]
    assert missing.value.code == 'chat_checkpoint_missing'
    assert calls == [] and provider.requests == []
    assert not model.model_completed and model.completed_checkpoint_id is None


async def test_context_budget_rejects_before_main_provider_start(provider: ProviderServer) -> None:
    calls = []

    async def on_start() -> None:
        calls.append('main')

    model = agent_provider(provider, input_budget=39, on_model_start=on_start)
    with pytest.raises(AppError) as oversized:
        _ = [delta async for delta in model.stream((ModelMessage('system', '规则'), ModelMessage('user', '问题')))]
    assert oversized.value.code == 'context_input_too_long'
    assert oversized.value.details == {'estimated_input_tokens': 40, 'input_budget': 39}
    assert calls == [] and provider.requests == []
    assert not model.model_completed


async def test_context_budget_includes_retained_checkpoint_history(provider: ProviderServer) -> None:
    saver = InMemorySaver()
    first = agent_provider(provider, checkpointer=saver)
    _ = [delta async for delta in first.stream((ModelMessage('system', '规则'), ModelMessage('user', '问题')))]
    calls = []

    async def on_start() -> None:
        calls.append('main')

    second = agent_provider(
        provider,
        checkpointer=saver,
        base_checkpoint_id=first.completed_checkpoint_id,
        message_ids=('system', 'question', 'answer', 'second-question'),
        incremental=True,
        input_budget=40,
        on_model_start=on_start,
    )
    with pytest.raises(AppError) as oversized:
        _ = [
            delta
            async for delta in second.stream(
                (
                    ModelMessage('system', '规则'),
                    ModelMessage('user', '问题'),
                    ModelMessage('assistant', '你好'),
                    ModelMessage('user', '追问'),
                )
            )
        ]
    assert oversized.value.code == 'context_input_too_long'
    assert oversized.value.details == {'estimated_input_tokens': 80, 'input_budget': 40}
    assert calls == []
    assert len(provider.requests) == 1


async def test_summary_token_trigger_uses_conservative_chinese_context_count(provider: ProviderServer) -> None:
    summaries = []

    async def summarize(messages: tuple[ModelMessage, ...]) -> str:
        summaries.append(messages)
        return '摘要'

    model = agent_provider(
        provider,
        message_ids=('system', 'old-question', 'old-answer', 'question'),
        trigger_tokens=100,
        keep_tokens=20,
        summarizer=summarize,
    )
    _ = [
        delta
        async for delta in model.stream(
            (
                ModelMessage('system', '规则'),
                ModelMessage('user', '问' * 20),
                ModelMessage('assistant', '答' * 20),
                ModelMessage('user', '新问'),
            )
        )
    ]
    assert len(summaries) == 1
    assert '问' * 20 in summaries[0][1].content
    assert '答' * 20 in summaries[0][1].content
    assert model.summary_count == 1 and len(provider.requests) == 1


async def test_regeneration_resumes_model_input_checkpoint_without_repaying_summary(
    provider: ProviderServer,
) -> None:
    saver = InMemorySaver()
    summaries = []

    async def summarize(messages: tuple[ModelMessage, ...]) -> str:
        summaries.append(messages)
        if len(summaries) != 1:
            raise AssertionError('A prepared regeneration must not summarize again')
        # Deliberately still above the trigger: regeneration must resume model,
        # not restart before_model and rely on another threshold decision.
        return '摘要' * 80

    frozen = (
        ModelMessage('system', '规则'),
        ModelMessage('user', '旧事实' * 100),
        ModelMessage('assistant', '旧回答' * 100),
        ModelMessage('user', '继续'),
    )
    identities = ('system', 'old-question', 'old-answer', 'question')
    first = agent_provider(
        provider,
        checkpointer=saver,
        message_ids=identities,
        trigger_tokens=128,
        keep_tokens=32,
        summarizer=summarize,
    )
    first_result = [delta async for delta in first.stream(frozen)]
    model_checkpoint = first_result[-1].result['context_model_checkpoint_id']
    assert model_checkpoint == first.model_checkpoint_id
    assert isinstance(model_checkpoint, str)
    assert first_result[-1].result['context_model_reservation_tokens'] < 1000

    regenerated = agent_provider(
        provider,
        checkpointer=saver,
        base_checkpoint_id=model_checkpoint,
        message_ids=identities,
        incremental=True,
        trigger_tokens=128,
        keep_tokens=32,
        summarizer=summarize,
    )
    result = [delta async for delta in regenerated.stream(frozen)]
    assert len(summaries) == 1
    assert first.summary_count == 1 and regenerated.summary_count == 0
    assert len(provider.requests) == 2
    assert provider.requests[0]['messages'] == provider.requests[1]['messages']
    assert ''.join(delta.text for delta in result) == '你好'
    assert regenerated.model_checkpoint_id is not None
    assert regenerated.model_checkpoint_id != regenerated.completed_checkpoint_id
    assert result[-1].result['context_model_reservation_tokens'] == first.model_reservation_tokens


ORPHAN_CHECKPOINT_ID = 'ffffffff-ffff-7fff-bfff-ffffffffffff'


class OrphanLatestSaver(InMemorySaver):
    def __init__(self) -> None:
        super().__init__()
        self.injected = False
        self.history_filters: list[dict[str, Any] | None] = []

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        result = await super().aput(config, checkpoint, metadata, new_versions)
        messages = checkpoint['channel_values'].get('messages', [])
        if not self.injected and messages and isinstance(messages[-1], AIMessage):
            self.injected = True
            orphan = deepcopy(checkpoint)
            orphan['id'] = ORPHAN_CHECKPOINT_ID
            orphan_config = deepcopy(config)
            orphan_config['metadata'] = {'agent_execution_id': 'other-worker-execution'}
            await super().aput(
                orphan_config,
                orphan,
                {**metadata, 'agent_execution_id': 'other-worker-execution'},
                new_versions,
            )
        return result

    async def alist(
        self,
        config: Any,
        *,
        filter: Any = None,  # noqa: A002 - official saver keyword
        before: Any = None,
        limit: Any = None,
    ) -> AsyncIterator[Any]:
        self.history_filters.append(filter)
        async for item in super().alist(config, filter=filter, before=before, limit=limit):
            yield item


async def test_completed_checkpoint_is_selected_by_execution_metadata_not_physical_latest(
    provider: ProviderServer,
) -> None:
    saver = OrphanLatestSaver()
    model = agent_provider(provider, checkpointer=saver)
    result = [delta async for delta in model.stream((ModelMessage('system', '规则'), ModelMessage('user', '问题')))]
    physical_latest = await saver.aget_tuple({'configurable': {'thread_id': 'contract-conversation'}})
    assert physical_latest is not None and physical_latest.checkpoint['id'] == ORPHAN_CHECKPOINT_ID
    own_id = result[-1].result['checkpoint_id']
    assert own_id != ORPHAN_CHECKPOINT_ID
    own = await saver.aget_tuple({'configurable': {'thread_id': 'contract-conversation', 'checkpoint_id': own_id}})
    assert own is not None
    execution_id = own.metadata['agent_execution_id']
    assert execution_id != 'other-worker-execution'
    assert {'agent_execution_id': execution_id} in saver.history_filters
    assert model.model_completed and len(provider.requests) == 1


async def test_summary_uses_visual_conclusions_without_turning_image_base64_into_text(
    provider: ProviderServer,
) -> None:
    observed: list[tuple[ModelMessage, ...]] = []

    async def summarize(messages: tuple[ModelMessage, ...]) -> str:
        observed.append(messages)
        return 'Earlier user supplied an image; its description is retained.'

    model = agent_provider(
        provider,
        message_ids=('old-question', 'old-reply', 'current'),
        trigger_tokens=256,
        keep_tokens=64,
        summarizer=summarize,
    )
    messages = (
        ModelMessage('user', 'Describe this image.', (ModelImage('data:image/jpeg;base64,AA=='),)),
        ModelMessage('assistant', 'The image shows a red square.'),
        ModelMessage('user', 'Continue.'),
    )
    result = [delta async for delta in model.stream(messages)]
    assert observed and len(observed) == 1
    rendered = '\n'.join(message.content for message in observed[0])
    assert 'Describe this image.' in rendered
    assert any('red square' in str(message['content']) for message in provider.requests[0]['messages'])
    assert 'data:image/jpeg;base64' not in rendered
    assert '[历史图片' in rendered
    assert len(provider.requests) == 1
    assert result[-1].result and result[-1].result['summary_count'] == 1
    assert result[-1].result['context_image_bytes'] == 0
    assert result[-1].result['context_model_image_bytes'] == 0


async def test_agent_composes_public_model_provider_without_vendor_type_or_credentials() -> None:
    class NativeProvider:
        def __init__(self) -> None:
            self.requests: list[tuple[ModelMessage, ...]] = []
            self.validations = 0

        async def validate(self) -> None:
            self.validations += 1

        async def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
            self.requests.append(messages)
            yield ModelDelta(reasoning='原生推理')
            yield ModelDelta(text='原生回答', finish_reason='stop')
            yield ModelDelta(input_tokens=7, output_tokens=3, cache_tokens=2, reasoning_tokens=1)

    native = NativeProvider()
    model = AgentProvider.from_provider(
        native,
        checkpointer=InMemorySaver(),
        thread_id='public-provider-conversation',
        base_checkpoint_id=ROOT_CHECKPOINT_ID,
        message_ids=('system', 'developer', 'question'),
        incremental=False,
        trigger_tokens=10000,
        keep_tokens=1000,
        summary_output_tokens=64,
        summarizer=unused_summary,
        on_model_start=started,
    )
    messages = (
        ModelMessage('system', '平台规则'),
        ModelMessage('developer', '业务约束'),
        ModelMessage('user', '图片问题', (ModelImage('data:image/jpeg;base64,AA=='),)),
    )
    await model.validate()
    result = [delta async for delta in model.stream(messages)]
    assert native.validations == 1 and native.requests == [messages]
    assert ''.join(delta.text for delta in result) == '原生回答'
    assert ''.join(delta.reasoning for delta in result) == '原生推理'
    assert [
        (d.input_tokens, d.output_tokens, d.cache_tokens, d.reasoning_tokens) for d in result if d.input_tokens
    ] == [(7, 3, 2, 1)]


async def test_retained_images_are_checked_at_actual_model_boundary_before_provider_start(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zhiheng.infrastructure.agents.adapters import agent as adapter

    saver = InMemorySaver()
    image = ModelImage('data:image/jpeg;base64,AA==')
    wire_bytes = len(image.data_url.encode())
    first = agent_provider(provider, checkpointer=saver, message_ids=('first',))
    result = [delta async for delta in first.stream((ModelMessage('user', 'First', (image,)),))]
    assert result[-1].result and result[-1].result['context_image_bytes'] == wire_bytes
    monkeypatch.setattr(adapter, 'MAX_CONTEXT_IMAGE_BYTES', wire_bytes * 2 - 1)
    starts = []

    async def start() -> None:
        starts.append('provider')

    second = agent_provider(
        provider,
        checkpointer=saver,
        base_checkpoint_id=first.completed_checkpoint_id,
        incremental=True,
        message_ids=('second',),
        on_model_start=start,
    )
    with pytest.raises(AppError) as rejected:
        _ = [delta async for delta in second.stream((ModelMessage('user', 'Second', (image,)),))]
    assert rejected.value.code == 'attachment_images_too_large'
    assert starts == [] and len(provider.requests) == 1


@pytest.mark.parametrize('budget,allowed', [(180, False), (400, True)])
async def test_summary_budget_accounts_for_prefix_and_sdk_preserved_input_before_summary_call(
    provider: ProviderServer, budget: int, allowed: bool
) -> None:
    summaries = []
    starts = []

    async def summarize(messages: tuple[ModelMessage, ...]) -> str:
        summaries.append(messages)
        return 'compact history'

    async def on_start() -> None:
        starts.append('main')

    model = agent_provider(
        provider,
        message_ids=('prefix', 'old-question', 'old-answer', 'current'),
        trigger_tokens=60,
        keep_tokens=40,
        input_budget=budget,
        summarizer=summarize,
        on_model_start=on_start,
    )
    messages = (
        ModelMessage('system', '规则' * 20),
        ModelMessage('user', 'old ' * 100),
        ModelMessage('assistant', 'old answer'),
        ModelMessage('user', 'next'),
    )
    if allowed:
        result = [delta async for delta in model.stream(messages)]
        assert result[-1].result and result[-1].result['summary_count'] == 1
        assert len(summaries) == len(starts) == len(provider.requests) == 1
    else:
        with pytest.raises(AppError) as rejected:
            _ = [delta async for delta in model.stream(messages)]
        assert rejected.value.code == 'context_input_too_long'
        assert not summaries and not starts and not provider.requests


class ReportedUsageProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.usage_emitted = False

    async def validate(self) -> None:
        pass

    async def stream(self, _messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
        self.calls += 1
        yield ModelDelta(text='complete answer', finish_reason='stop')
        self.usage_emitted = True
        yield ModelDelta(input_tokens=8, output_tokens=2, cache_tokens=3, reasoning_tokens=1)


class BlockingCompletedSaver(InMemorySaver):
    def __init__(self, entered: asyncio.Event) -> None:
        super().__init__()
        self.entered = entered

    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        if any(isinstance(message, AIMessage) for message in checkpoint['channel_values'].get('messages', [])):
            self.entered.set()
            await asyncio.Event().wait()
        return await super().aput(config, checkpoint, metadata, new_versions)


@pytest.mark.parametrize('stage', ['durable_model_checkpoint', 'result_checkpoint'])
async def test_cancellation_preserves_reported_usage_and_propagates_without_accepting_checkpoint(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    entered = asyncio.Event()
    provider = ReportedUsageProvider()
    model = AgentProvider.from_provider(
        provider,
        checkpointer=BlockingCompletedSaver(entered) if stage == 'durable_model_checkpoint' else InMemorySaver(),
        thread_id='cancelled-known-usage',
        base_checkpoint_id=ROOT_CHECKPOINT_ID,
        message_ids=('question',),
        incremental=False,
        trigger_tokens=16000,
        keep_tokens=5000,
        summary_output_tokens=1024,
        summarizer=unused_summary,
        on_model_start=started,
    )
    if stage == 'result_checkpoint':

        async def wait_for_result(*_args: Any) -> None:
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(model, '_checkpoint_result', wait_for_result)
    observed: list[ModelDelta] = []

    async def consume() -> None:
        async for delta in model.stream((ModelMessage('user', 'hello'),)):
            observed.append(delta)  # noqa: PERF401 - preserve the deltas emitted before cancellation propagates

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert provider.usage_emitted
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)
        assert task.cancelled()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert provider.calls == 1
    assert [
        (d.input_tokens, d.output_tokens, d.cache_tokens, d.reasoning_tokens)
        for d in observed
        if d.input_tokens is not None
    ] == [(8, 2, 3, 1)]
    assert all(delta.result is None for delta in observed)
    assert model.completed_checkpoint_id is None


async def test_closing_agent_stream_does_not_yield_during_generator_exit() -> None:
    model = AgentProvider.from_provider(
        ReportedUsageProvider(),
        checkpointer=InMemorySaver(),
        thread_id='closed-stream',
        base_checkpoint_id=ROOT_CHECKPOINT_ID,
        message_ids=('question',),
        incremental=False,
        trigger_tokens=16000,
        keep_tokens=5000,
        summary_output_tokens=1024,
        summarizer=unused_summary,
        on_model_start=started,
    )
    stream = model.stream((ModelMessage('user', 'hello'),))
    first = await anext(stream)
    assert first.text == 'complete answer'
    await stream.aclose()
