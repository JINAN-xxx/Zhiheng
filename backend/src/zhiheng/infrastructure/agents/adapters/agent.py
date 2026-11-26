"""Shared official Agent state and summaries over the public native model protocol."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4

from langchain.agents import AgentState, create_agent
from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware
from langchain.agents.middleware.summarization import DEFAULT_SUMMARY_PROMPT
from langchain.agents.middleware.types import ModelRequest as AgentModelRequest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    convert_to_messages,
    get_buffer_string,
)
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langsmith import tracing_context
from pydantic import PrivateAttr

from zhiheng.modules.models.public import (
    IMAGE_TOKEN_RESERVATION,
    MAX_CONTEXT_IMAGE_BYTES,
    ModelDelta,
    ModelImage,
    ModelMessage,
    ModelProvider,
    ProviderError,
    context_image_bytes,
    estimate_messages,
)
from zhiheng.shared.agent import SUMMARY_MESSAGE_OVERHEAD
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from collections.abc import Iterable

    from langchain.agents.middleware.types import InputAgentState
    from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
    from langchain_core.messages import MessageLikeRepresentation
    from langchain_core.runnables import RunnableConfig
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.runtime import Runtime
    from langgraph.types import StateSnapshot

ROOT_CHECKPOINT_ID = '00000000-0000-0000-0000-000000000000'
SummaryWriter = Callable[[tuple[ModelMessage, ...]], Awaitable[str]]
ModelStart = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class AgentContext:
    thread_id: str


class _ModelCall(AgentMiddleware[AgentState[Any], AgentContext, Any]):
    """Inject this Run's trusted prefix and enter its provider boundary once."""

    def __init__(
        self,
        prefix: list[AnyMessage],
        on_model_start: ModelStart,
        input_budget: int | None,
        reference: str | None = None,
    ) -> None:
        self.prefix, self.on_model_start = prefix, on_model_start
        self.input_budget = input_budget
        self.reference = reference
        self.started = False
        self.input_messages: tuple[AnyMessage, ...] | None = None
        self.before_summary: tuple[AnyMessage, ...] = ()

    async def abefore_model(self, state: AgentState[Any], runtime: 'Runtime[AgentContext]') -> None:
        # Observe the SDK input; SMM alone still chooses the prefix to summarize.
        self.before_summary = tuple(state['messages'])

    def request_messages(self, history: Sequence[AnyMessage]) -> list[AnyMessage]:
        if self.reference is None:
            return [*self.prefix, *history]
        current = history[-1]
        if not isinstance(current, HumanMessage):
            raise ProviderError(reason='invalid_request')
        text, images = _message_content(current)
        transient = _normalize_message(ModelMessage('user', f'{self.reference}\n\n{text}', images))
        return [*self.prefix, *history[:-1], transient]

    def require_summary_capacity(self, selected: Sequence[BaseMessage], summary_output_tokens: int) -> None:
        if self.input_budget is None:
            return
        if list(self.before_summary[: len(selected)]) != list(selected):
            raise RuntimeError('Summary selection does not match the observed Agent input')
        preserved = self.before_summary[len(selected) :]
        required = (
            context_input_tokens(self.request_messages(preserved)) + summary_output_tokens + SUMMARY_MESSAGE_OVERHEAD
        )
        if required > self.input_budget:
            raise AppError(
                'context_input_too_long',
                '本轮必需内容、保留历史和摘要预算超过模型输入容量，请缩短内容或调整摘要预算',
                422,
                details={'estimated_input_tokens': required, 'input_budget': self.input_budget},
            )

    async def awrap_model_call(
        self,
        request: AgentModelRequest[AgentContext],
        handler: Callable[[AgentModelRequest[AgentContext]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        if self.started:
            raise ProviderError(reason='invalid_request')
        messages = self.request_messages(request.messages)
        estimated = context_input_tokens(messages)
        if context_image_bytes(_native_messages(messages)) > MAX_CONTEXT_IMAGE_BYTES:
            raise AppError('attachment_images_too_large', '本次请求含历史的安全图片数据合计最多16MiB', 422)
        if self.input_budget is not None and estimated > self.input_budget:
            raise AppError(
                'context_input_too_long',
                '当前对话上下文超过已配置的模型输入容量',
                422,
                details={'estimated_input_tokens': estimated, 'input_budget': self.input_budget},
            )
        await self.on_model_start()
        self.started = True
        self.input_messages = tuple(request.messages)
        return await handler(
            request.override(
                messages=messages,
            )
        )


def _completion_reason(value: object) -> Literal['stop', 'length'] | None:
    if value is None or value in ('stop', 'length'):
        return cast("Literal['stop', 'length'] | None", value)
    raise ProviderError(reason='unsupported_finish_reason')


def _normalize_message(message: ModelMessage) -> AnyMessage:
    if message.role == 'system':
        return SystemMessage(content=message.content)
    if message.role == 'developer':
        return ChatMessage(role='developer', content=message.content)
    if message.role == 'assistant':
        return AIMessage(content=message.content)
    if message.images:
        content: list[str | dict[str, Any]] = [{'type': 'text', 'text': message.content}]
        content.extend(
            {'type': 'image_url', 'image_url': {'url': image.data_url, 'detail': 'auto'}} for image in message.images
        )
        return HumanMessage(content=content)
    return HumanMessage(content=message.content)


def _native_messages(messages: Sequence[BaseMessage]) -> tuple[ModelMessage, ...]:
    result = []
    for message in messages:
        role: Literal['system', 'developer', 'user', 'assistant']
        if isinstance(message, HumanMessage):
            role = 'user'
        elif isinstance(message, AIMessage):
            role = 'assistant'
        elif isinstance(message, SystemMessage):
            role = 'system'
        elif isinstance(message, ChatMessage) and message.role == 'developer':
            role = 'developer'
        else:
            raise ProviderError(reason='invalid_request')
        text, images = _message_content(message)
        if images and role != 'user':
            raise ProviderError(reason='invalid_content')
        result.append(ModelMessage(role, text, images))
    return tuple(result)


class _NativeChatModel(BaseChatModel):
    """Translate standard LC messages/chunks while the native provider owns HTTP/usage."""

    _provider: ModelProvider = PrivateAttr()
    _usage: ModelDelta | None = PrivateAttr(default=None)
    _message_id: str = PrivateAttr(default_factory=lambda: str(uuid4()))

    def __init__(self, provider: ModelProvider) -> None:
        super().__init__()
        self._provider = provider

    @property
    def _llm_type(self) -> str:
        return 'zhiheng-native-chat'

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: 'CallbackManagerForLLMRun | None' = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError('Native chat Agent supports asynchronous execution only')

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: 'AsyncCallbackManagerForLLMRun | None' = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if stop or kwargs:
            raise ProviderError(reason='invalid_options')
        async for delta in self._provider.stream(_native_messages(messages)):
            counters = ('input_tokens', 'output_tokens', 'cache_tokens', 'reasoning_tokens')
            if any(getattr(delta, name) is not None for name in counters):
                previous = self._usage or ModelDelta()
                self._usage = ModelDelta(
                    **{
                        name: getattr(delta, name) if getattr(delta, name) is not None else getattr(previous, name)
                        for name in counters
                    }
                )
            if delta.text or delta.reasoning or delta.finish_reason is not None:
                yield ChatGenerationChunk(
                    message=AIMessageChunk(
                        id=self._message_id,
                        content=delta.text,
                        additional_kwargs={'reasoning_content': delta.reasoning} if delta.reasoning else {},
                        response_metadata={'finish_reason': delta.finish_reason} if delta.finish_reason else {},
                    )
                )

    @property
    def usage(self) -> ModelDelta | None:
        # No usage_metadata: LC-normalized counters are never financial facts.
        return self._usage

    @property
    def message_id(self) -> str:
        return self._message_id


def _chunk_delta(chunk: AIMessageChunk) -> ModelDelta:
    text, block_reasoning = '', ''
    if isinstance(chunk.content, str):
        text = chunk.content
    else:
        for block in chunk.content:
            if isinstance(block, str):
                text += block
            elif block.get('type') == 'text' and isinstance(block.get('text'), str):
                text += block['text']
            elif block.get('type') == 'reasoning' and isinstance(block.get('reasoning'), str):
                block_reasoning += block['reasoning']
            else:
                raise ProviderError(reason='invalid_content')
    reasoning = chunk.additional_kwargs.get('reasoning_content', '')
    if not isinstance(reasoning, str):
        raise ProviderError(reason='invalid_reasoning')
    if reasoning and block_reasoning and reasoning != block_reasoning:
        raise ProviderError(reason='invalid_reasoning')
    if chunk.tool_calls or chunk.tool_call_chunks or chunk.invalid_tool_calls:
        raise ProviderError(reason='unsupported_finish_reason')
    return ModelDelta(
        text,
        reasoning=reasoning or block_reasoning,
        finish_reason=_completion_reason(chunk.response_metadata.get('finish_reason')),
    )


def _message_content(message: BaseMessage) -> tuple[str, tuple[ModelImage, ...]]:
    """Read public model text/images, excluding encoded image bytes and reasoning."""
    if isinstance(message.content, str):
        return message.content, ()
    text: list[str] = []
    images: list[ModelImage] = []
    for block in message.content:
        if isinstance(block, str):
            text.append(block)
        elif block.get('type') == 'text' and isinstance(block.get('text'), str):
            text.append(block['text'])
        elif block.get('type') == 'image_url':
            image = block.get('image_url')
            url = image.get('url') if isinstance(image, dict) else image
            if not isinstance(url, str):
                raise ProviderError(reason='invalid_content')
            images.append(ModelImage(url))
        elif block.get('type') == 'reasoning':
            continue
        else:
            raise ProviderError(reason='invalid_content')
    return ''.join(text), tuple(images)


def _message_input(message: BaseMessage) -> tuple[str, int]:
    text, images = _message_content(message)
    return text, len(images)


def checkpoint_reservation_tokens(messages: Sequence[BaseMessage]) -> int:
    """Match native admission's conservative text/image reservation estimate."""
    total = 0
    for message in messages:
        text, images = _message_input(message)
        total += 16 + len(text.encode('utf-8')) + images * IMAGE_TOKEN_RESERVATION
    return total


def context_input_tokens(messages: 'Iterable[MessageLikeRepresentation]') -> int:
    """Bridge standard chat messages to the single domain context-budget policy."""
    return estimate_messages(_native_messages(convert_to_messages(messages)))


class AgentProvider:
    """Use official Agent/checkpoint/summary mechanisms inside one admitted conversational Run."""

    def __init__(  # noqa: PLR0913 - frozen execution settings and native provider options form this boundary
        self,
        *,
        provider: ModelProvider,
        checkpointer: BaseCheckpointSaver[Any] | None,
        thread_id: str,
        base_checkpoint_id: str | None,
        message_ids: tuple[str, ...],
        incremental: bool,
        trigger_tokens: int,
        keep_tokens: int,
        summary_output_tokens: int,
        summarizer: SummaryWriter | None,
        on_model_start: ModelStart,
        input_budget: int | None = None,
        reference_message_index: int | None = None,
    ) -> None:
        self._provider = provider
        if (
            not thread_id
            or base_checkpoint_id == ''
            or any(not value for value in message_ids)
            or len(set(message_ids)) != len(message_ids)
            or type(trigger_tokens) is not int
            or type(keep_tokens) is not int
            or type(summary_output_tokens) is not int
            or not 0 < keep_tokens < trigger_tokens
            or summary_output_tokens <= 0
            or (input_budget is not None and (type(input_budget) is not int or input_budget < 0))
        ):
            raise ValueError('Invalid chat Agent execution configuration')
        self._checkpointer = checkpointer
        self._thread_id = thread_id
        self._base_checkpoint_id = base_checkpoint_id or ROOT_CHECKPOINT_ID
        self._message_ids = message_ids
        self._incremental = incremental
        self._trigger_tokens, self._keep_tokens = trigger_tokens, keep_tokens
        self._summary_output_tokens = summary_output_tokens
        self._input_budget = input_budget
        self._reference_message_index = reference_message_index
        self._summarizer, self._on_model_start = summarizer, on_model_start
        self.completed_checkpoint_id: str | None = None
        self.model_checkpoint_id: str | None = None
        self.model_reservation_tokens = 0
        self.context_image_bytes = self.model_image_bytes = 0
        self.model_completed = False
        self.summary_count = 0
        self.summary_estimated_tokens = 0
        self.context_reservation_tokens = 0
        self.context_estimated_tokens = self.model_input_tokens = self.retained_rounds = 0

    @classmethod
    def from_provider(cls, provider: ModelProvider, **agent_options: Any) -> 'AgentProvider':
        return cls(provider=provider, **agent_options)

    async def validate(self) -> None:
        await self._provider.validate()

    async def _summarize(self, messages: Sequence[BaseMessage]) -> str:
        # The callable branch bypasses SMM's implicit with_retry/overflow retry.
        # SMM has already selected the prefix; never tail-trim it before this port.
        summary_messages: list[BaseMessage] = []
        for message in messages:
            text, images = _message_input(message)
            if images:
                text += '\n[历史图片：保留对应助手回答中的视觉结论，图片编码不作为摘要正文]'
            summary_messages.append(message.model_copy(update={'content': text}))
        prompt = DEFAULT_SUMMARY_PROMPT.format(messages=get_buffer_string(summary_messages, format='xml')).rstrip()
        assert self._summarizer is not None
        summary = await self._summarizer(
            (
                ModelMessage(
                    'system',
                    '你负责为同一用户的会话整理滚动摘要。只返回摘要，'
                    f'保留目标、事实、约束和未完成事项，控制在 {self._summary_output_tokens} Token 内。',
                ),
                ModelMessage('user', prompt),
            )
        )
        if not isinstance(summary, str) or not summary.strip():
            raise ProviderError(reason='invalid_response')
        self.summary_count += 1
        self.summary_estimated_tokens = count_tokens_approximately([HumanMessage(content=summary)])
        return summary

    def _input(
        self, messages: tuple[ModelMessage, ...]
    ) -> tuple[list[AnyMessage], list[AnyMessage | RemoveMessage], str | None]:
        if len(messages) != len(self._message_ids):
            raise ValueError('Chat Agent message identities do not match the frozen input')
        prefix: list[AnyMessage] = []
        history: list[AnyMessage | RemoveMessage] = []
        reference = None
        for index, (message, identity) in enumerate(zip(messages, self._message_ids, strict=True)):
            if index == self._reference_message_index:
                if message.role != 'system' or message.images:
                    raise ProviderError(reason='invalid_request')
                reference = message.content
                continue
            normalized = _normalize_message(message)
            normalized.id = identity
            (prefix if message.role in {'system', 'developer'} else history).append(normalized)
        if not history or not isinstance(history[-1], HumanMessage):
            raise ValueError('Chat Agent input must end with the current user message')
        if self._incremental:
            if self._base_checkpoint_id == ROOT_CHECKPOINT_ID:
                raise ValueError('Incremental chat requires an accepted checkpoint')
            return prefix, history, reference
        # Full frozen seeds create a new branch without appending duplicate history.
        if self._checkpointer is not None:
            history.insert(0, RemoveMessage(id=REMOVE_ALL_MESSAGES))
        return prefix, history, reference

    async def _agent_input(
        self,
        agent: 'CompiledStateGraph[Any, AgentContext, Any, Any]',
        config: 'RunnableConfig',
        inputs: list[AnyMessage | RemoveMessage],
    ) -> 'InputAgentState | None':
        if (
            self._checkpointer is not None
            and self._base_checkpoint_id != ROOT_CHECKPOINT_ID
            and await self._checkpointer.aget_tuple(config) is None
        ):
            raise AppError('chat_checkpoint_missing', '会话检查点已不可用，请恢复上下文后重试', 409)
        if self._incremental:
            base_state = await agent.aget_state(config)
            if 'model' in base_state.next:
                base_messages = base_state.values.get('messages')
                if not isinstance(base_messages, list) or not base_messages or base_messages[-1] != inputs[-1]:
                    raise AppError('chat_checkpoint_mismatch', '原回复的模型输入检查点与问题不一致', 409)
                # Explicit regeneration resumes the prepared model node, not SMM.
                return None
        return cast('InputAgentState', {'messages': inputs})

    @staticmethod
    def _checkpoint_messages(state: 'StateSnapshot') -> list[BaseMessage]:
        messages = state.values.get('messages')
        if not isinstance(messages, list) or any(not isinstance(message, BaseMessage) for message in messages):
            raise RuntimeError('Chat Agent checkpoint has invalid messages')
        return messages

    async def _completed_state(
        self,
        agent: 'CompiledStateGraph[Any, AgentContext, Any, Any]',
        execution_id: str,
    ) -> 'StateSnapshot':
        # A late fenced worker can still leave a physically newer saver branch.
        # Only this execution's official checkpoint metadata identifies our result.
        async for state in agent.aget_state_history(
            {'configurable': {'thread_id': self._thread_id}},
            filter={'agent_execution_id': execution_id},
        ):
            if not state.next:
                return state
        raise RuntimeError('Chat Agent did not publish this execution completed checkpoint')

    async def _checkpoint_result(
        self,
        agent: 'CompiledStateGraph[Any, AgentContext, Any, Any]',
        execution_id: str,
        model_call: _ModelCall,
    ) -> None:
        state = await self._completed_state(agent, execution_id)
        checkpoint_id = state.config.get('configurable', {}).get('checkpoint_id')
        if not isinstance(checkpoint_id, str) or checkpoint_id == ROOT_CHECKPOINT_ID or state.parent_config is None:
            raise RuntimeError('Chat Agent completed checkpoint is invalid')
        messages = self._checkpoint_messages(state)
        model_state = await agent.aget_state(state.parent_config)
        model_messages = self._checkpoint_messages(model_state)
        model_checkpoint_id = model_state.config.get('configurable', {}).get('checkpoint_id')
        if (
            'model' not in model_state.next
            or model_call.input_messages is None
            or model_messages != list(model_call.input_messages)
            or not isinstance(model_checkpoint_id, str)
            or model_checkpoint_id == ROOT_CHECKPOINT_ID
        ):
            raise RuntimeError('Chat Agent parent is not the effective model input checkpoint')
        self.completed_checkpoint_id, self.model_checkpoint_id = checkpoint_id, model_checkpoint_id
        self.context_reservation_tokens = checkpoint_reservation_tokens(messages)
        self.context_image_bytes = context_image_bytes(_native_messages(messages))
        model_input = model_call.request_messages(model_messages)
        self.model_image_bytes = context_image_bytes(_native_messages(model_input))
        self.model_reservation_tokens = checkpoint_reservation_tokens(model_input)
        self.context_estimated_tokens = context_input_tokens(messages)
        self.model_input_tokens = context_input_tokens(model_input)
        self.retained_rounds = sum(isinstance(message, AIMessage) for message in messages)

    async def _agent_deltas(
        self,
        agent: 'CompiledStateGraph[Any, AgentContext, Any, Any]',
        agent_input: 'InputAgentState | None',
        config: 'RunnableConfig',
        model: _NativeChatModel,
    ) -> AsyncIterator[ModelDelta]:
        async for part in agent.astream(
            agent_input,
            config=config,
            context=AgentContext(self._thread_id),
            stream_mode=['messages', 'updates'],
            version='v2',
            durability='sync' if self._checkpointer is not None else None,
        ):
            if part['type'] != 'messages':
                continue
            chunk, metadata = part['data']
            if (
                metadata.get('langgraph_node') != 'model'
                or not isinstance(chunk, AIMessageChunk)
                or chunk.id != model.message_id
            ):
                continue
            delta = _chunk_delta(chunk)
            self.model_completed = self.model_completed or delta.finish_reason is not None
            if delta.text or delta.reasoning or delta.finish_reason is not None:
                yield delta

    async def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
        client = _NativeChatModel(self._provider)
        prefix, inputs, reference = self._input(messages)
        self.completed_checkpoint_id = self.model_checkpoint_id = None
        self.model_completed = False
        self.context_image_bytes = self.model_image_bytes = 0
        self.summary_count = self.summary_estimated_tokens = self.context_reservation_tokens = 0
        self.model_reservation_tokens = self.context_estimated_tokens = self.model_input_tokens = (
            self.retained_rounds
        ) = 0
        model_call = _ModelCall(prefix, self._on_model_start, self._input_budget, reference)

        async def summarize_selected(selected: Sequence[BaseMessage]) -> str:
            model_call.require_summary_capacity(selected, self._summary_output_tokens)
            return await self._summarize(selected)

        summary: SummarizationMiddleware[Any, AgentContext] = SummarizationMiddleware(
            model=client,
            trigger=('tokens', self._trigger_tokens),
            keep=('tokens', self._keep_tokens),
            token_counter=context_input_tokens,
            trim_tokens_to_summarize=None,
            summarizer=summarize_selected,
        )
        agent = create_agent(
            model=client,
            tools=[],
            middleware=[model_call, summary] if self._summarizer is not None else [model_call],
            context_schema=AgentContext,
            checkpointer=self._checkpointer,
        )
        execution_id = str(uuid4())
        config: RunnableConfig = {
            'configurable': {'thread_id': self._thread_id, 'checkpoint_id': self._base_checkpoint_id},
            'metadata': {'agent_execution_id': execution_id},
        }
        failure: Exception | asyncio.CancelledError | None = None
        try:
            agent_input = await self._agent_input(agent, config, inputs)
            with tracing_context(enabled=False):
                async for delta in self._agent_deltas(agent, agent_input, config, client):
                    yield delta
                if not self.model_completed:
                    raise ProviderError(reason='incomplete_stream')
                if self._checkpointer is not None:
                    await self._checkpoint_result(agent, execution_id, model_call)
        except (Exception, asyncio.CancelledError) as error:
            # Preserve received raw usage through saver failure or task cancellation.
            # GeneratorExit must close the stream immediately without yielding.
            failure = error
        # Preserve the native provider's final raw counters even if saver failed.
        if client.usage is not None:
            yield client.usage
        if failure is not None:
            raise failure
        yield ModelDelta(
            result={
                'checkpoint_id': self.completed_checkpoint_id,
                'summary_count': self.summary_count,
                'summary_estimated_tokens': self.summary_estimated_tokens,
                'context_reservation_tokens': self.context_reservation_tokens,
                'context_image_bytes': self.context_image_bytes,
                'context_model_image_bytes': self.model_image_bytes,
                'context_model_checkpoint_id': self.model_checkpoint_id,
                'context_model_reservation_tokens': self.model_reservation_tokens,
                'context_estimated_tokens': self.context_estimated_tokens,
                'context_model_input_tokens': self.model_input_tokens,
                'context_retained_rounds': self.retained_rounds,
            }
        )
