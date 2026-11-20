"""Official task Agent, tool middleware and durable checkpoints over native model calls."""

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast
from uuid import UUID

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware, SummarizationMiddleware, TodoListMiddleware, hook_config
from langchain.agents.middleware.todo import PlanningState
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
    convert_to_messages,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command
from langsmith import tracing_context
from pydantic import ConfigDict, Field, PrivateAttr, create_model

from zhiheng.infrastructure.agents.adapters.checkpoints import CheckpointStore
from zhiheng.modules.models.public import (
    AgentModelMessage,
    ModelMessage,
    ModelToolCall,
    estimate_messages,
    estimate_tool_schemas,
)
from zhiheng.modules.work.public import (
    WORK_CODE_TOOLS,
    WORK_CORE_TOOLS,
    WORK_TOOLSETS,
    AgentKernelOutcome,
    CheckpointWriter,
    ModelInvoker,
    ProgressWriter,
    ToolAvailability,
    ToolContextWriter,
    ToolInvoker,
    valid_work_summary,
)
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from langchain.agents.middleware.types import InputAgentState, ModelRequest, ModelResponse, ToolCallRequest
    from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
    from langchain_core.messages import MessageLikeRepresentation
    from langchain_core.runnables import RunnableConfig
    from langgraph.graph.state import CompiledStateGraph
    from langgraph.runtime import Runtime
    from langgraph.types import StateSnapshot

ACTIVE_ACTION: ContextVar[str] = ContextVar('workspace_action', default='')
FATAL_TOOL_ERRORS = frozenset(
    {
        'forbidden',
        'unauthenticated',
        'execution_lost',
        'work_budget_exhausted',
        'work_execution_uncertain',
        'work_input_changed',
        'work_sandbox_unavailable',
    }
)


def _merge_skills(previous: list[str], current: list[str]) -> list[str]:
    return sorted(set(previous or []) | set(current or []))


def _merge_toolsets(previous: dict[str, bool], current: dict[str, bool]) -> dict[str, bool]:
    return {**(previous or {}), **(current or {})}


class WorkspaceState(PlanningState, total=False):
    loaded_skills: Annotated[list[str], _merge_skills]
    loaded_toolsets: Annotated[dict[str, bool], _merge_toolsets]
    toolsets_revision: int
    awaiting_input: bool
    reviewed: bool
    review_note: str


def _text(message: BaseMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return ''.join(block if isinstance(block, str) else str(block.get('text', '')) for block in message.content)


def _wire(message: BaseMessage) -> AgentModelMessage:
    if isinstance(message, ToolMessage):
        return AgentModelMessage(role='tool', content=_text(message), tool_call_id=message.tool_call_id)
    if isinstance(message, AIMessage):
        return AgentModelMessage(
            role='assistant',
            content=_text(message),
            tool_calls=tuple(
                ModelToolCall(id=call['id'], name=call['name'], arguments=json.dumps(call['args'], ensure_ascii=True))
                for call in message.tool_calls
            ),
            reasoning_content=str(message.additional_kwargs.get('reasoning_content', '')),
        )
    return AgentModelMessage(role='system' if isinstance(message, SystemMessage) else 'user', content=_text(message))


def _tokens(messages: 'Iterable[MessageLikeRepresentation]') -> int:
    wire = tuple(_wire(message) for message in convert_to_messages(messages))
    transcript = tuple(
        ModelMessage(
            'system'
            if message.role in {'system', 'developer'}
            else 'assistant'
            if message.role == 'assistant'
            else 'user',
            message.content
            + (
                json.dumps([call.model_dump(mode='json') for call in message.tool_calls], ensure_ascii=False)
                if message.tool_calls
                else ''
            ),
        )
        for message in wire
    )
    return estimate_messages(transcript)


class _WorkspaceModel(BaseChatModel):
    _invoke: ModelInvoker = PrivateAttr()
    _purpose: str = PrivateAttr(default='agent')
    _tools: tuple[dict[str, object], ...] = PrivateAttr(default=())

    def __init__(self, invoke: ModelInvoker, purpose: str = 'agent') -> None:
        super().__init__()
        self._invoke, self._purpose = invoke, purpose

    @property
    def _llm_type(self) -> str:
        return 'zhiheng-workspace'

    def bind_tools(
        self, tools: Sequence[Any], *, tool_choice: str | None = None, **kwargs: Any
    ) -> Runnable[LanguageModelInput, AIMessage]:
        bound = self.model_copy()
        bound._tools = tuple(convert_to_openai_tool(tool) for tool in tools)
        return bound

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: 'CallbackManagerForLLMRun | None' = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError('Workspace model execution is asynchronous')

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: 'AsyncCallbackManagerForLLMRun | None' = None,
        **kwargs: Any,
    ) -> ChatResult:
        wire = tuple(_wire(message) for message in messages)
        identity = json.dumps(
            {
                'messages': [
                    {'id': message.id, **value.model_dump(mode='json')}
                    for message, value in zip(messages, wire, strict=True)
                ],
                'tools': self._tools,
                'purpose': self._purpose,
            },
            ensure_ascii=True,
            sort_keys=True,
        )
        key = hashlib.sha256(identity.encode()).hexdigest()
        result = await self._invoke(wire, self._tools, key, self._purpose)
        if self._purpose == 'summary' and not valid_work_summary(result.content):
            raise AppError(
                'work_summary_failed', '任务历史摘要未能有效生成，原始资料和执行记录已保留，可以继续任务', 502
            )
        response = AIMessage(
            id=str(result.run_id),
            content=result.content,
            tool_calls=[
                {'id': call.id, 'name': call.name, 'args': json.loads(call.arguments)} for call in result.tool_calls
            ],
            additional_kwargs={'reasoning_content': result.reasoning} if result.reasoning else {},
        )
        return ChatResult(generations=[ChatGeneration(message=response)])


@dataclass
class _ExecutionContext:
    invoke: ToolInvoker
    skill_texts: dict[str, str]
    write_tool_context: ToolContextWriter | None = None
    system_prompt: str = ''
    schemas: tuple[dict[str, object], ...] = ()
    available_tools: tuple[Any, ...] = ()
    allowed_toolsets: frozenset[str] = frozenset({'inspection'})
    legacy_calls: frozenset[tuple[str, str, str]] = frozenset()
    execution_instructions: str = ''
    tool_available: ToolAvailability | None = None


class _ExecutionMiddleware(AgentMiddleware[Any, Any, Any]):
    def __init__(self, context: _ExecutionContext) -> None:
        super().__init__()
        self.context = context

    def _active_names(self, state: Mapping[str, Any]) -> set[str]:
        names = set(WORK_CORE_TOOLS)
        loaded = state.get('loaded_toolsets', {})
        if isinstance(loaded, dict):
            for group in self.context.allowed_toolsets:
                if loaded.get(group) is True:
                    names.update(WORK_TOOLSETS[group])
        return names

    def select_tools(self, state: Mapping[str, Any], tools: Sequence[Any] | None = None) -> list[Any]:
        names = self._active_names(state) | {call[2] for call in self.context.legacy_calls}
        selected = [
            tool
            for tool in (self.context.available_tools if tools is None else tools)
            if convert_to_openai_tool(tool)['function']['name'] in names
            and (
                self.context.tool_available is None
                or self.context.tool_available(convert_to_openai_tool(tool)['function']['name'])
            )
        ]
        if tools is not None or self.context.available_tools:
            self.context.schemas = tuple(convert_to_openai_tool(tool) for tool in selected)
        return selected

    def _system(self, state: Mapping[str, Any]) -> str:
        names = state.get('loaded_skills', [])
        instructions = (
            '\n\n'.join(
                self.context.skill_texts[name]
                for name in names
                if isinstance(name, str) and name in self.context.skill_texts
            )
            if isinstance(names, list)
            else ''
        )
        note = str(state.get('review_note', ''))
        execution = (
            self.context.execution_instructions
            if (
                WORK_CODE_TOOLS.intersection(self._active_names(state))
                or any(call[2] in WORK_CODE_TOOLS for call in self.context.legacy_calls)
            )
            else ''
        )
        return (
            self.context.system_prompt
            + ('\n\n' + execution if execution else '')
            + ('\n\n已加载技能：\n' + instructions + '\n' + note if instructions or note else '')
        )

    async def awrap_model_call(
        self,
        request: 'ModelRequest[Any]',
        handler: Callable[['ModelRequest[Any]'], Awaitable['ModelResponse[Any]']],
    ) -> 'ModelResponse[Any]':
        self.context.system_prompt = _text(request.system_message) if request.system_message else ''
        selected = self.select_tools(request.state, request.tools)
        request = request.override(tools=selected, system_message=SystemMessage(content=self._system(request.state)))
        return await handler(request)

    @hook_config(can_jump_to=['model'])
    def after_model(self, state: WorkspaceState, runtime: Any) -> dict[str, Any] | None:
        if (
            state.get('reviewed')
            or state.get('awaiting_input')
            or not any(item.get('status') != 'completed' for item in state.get('todos', []))
        ):
            return None
        messages = state['messages']
        last = messages[-1]
        if not isinstance(last, AIMessage) or last.tool_calls or last.id is None:
            return None
        return {
            'messages': [RemoveMessage(id=last.id)],
            'reviewed': True,
            'review_note': '交付前核对一次原始要求和任务清单：未完成且仍可执行的项目继续完成；真实工具失败不可宣称成功。缺信息时调用 request_input；受阻时说明未完成项。',
            'jump_to': 'model',
        }

    def _tool_update(self, call: Mapping[str, Any], result: ToolMessage, legacy: bool) -> ToolMessage | Command[Any]:
        try:
            envelope = json.loads(str(result.content))
        except ValueError:
            envelope = None
        if isinstance(envelope, dict) and envelope.get('success') is False:
            result = result.model_copy(update={'status': 'error'})
        if call['name'] == 'load_tools' and result.status != 'error':
            data = envelope.get('data') if isinstance(envelope, dict) else None
            groups = data.get('loaded_toolsets') if isinstance(data, dict) else None
            if (
                not isinstance(groups, list)
                or not groups
                or any(not isinstance(group, str) or group not in self.context.allowed_toolsets for group in groups)
            ):
                raise AppError('work_toolset_invalid', '工具加载结果无效，未开放额外工具', 502)
            return Command(update={'messages': [result], 'loaded_toolsets': dict.fromkeys(groups, True)})
        if call['name'] == 'load_skill' and result.status != 'error':
            return Command(update={'messages': [result], 'loaded_skills': [str(call['args'].get('name', ''))]})
        if call['name'] == 'request_input' and result.status != 'error':
            return Command(update={'messages': [result], 'awaiting_input': True})
        if legacy:
            group = (
                'python'
                if call['name'] == 'run_python'
                else 'shell'
                if call['name'] == 'execute'
                else 'files'
                if call['name'] in WORK_CODE_TOOLS
                else 'inspection'
            )
            return Command(update={'messages': [result], 'loaded_toolsets': {group: True}})
        return result

    async def awrap_tool_call(
        self,
        request: 'ToolCallRequest',
        handler: Callable[['ToolCallRequest'], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        parent = next(
            (message.id for message in reversed(request.state['messages']) if isinstance(message, AIMessage)), ''
        )
        legacy = (str(parent), str(call['id']), str(call['name'])) in self.context.legacy_calls
        self.select_tools(request.state)
        if self.context.write_tool_context is not None:
            self.context.write_tool_context(
                str(parent),
                (
                    AgentModelMessage(role='system', content=self._system(request.state)),
                    *(_wire(message) for message in request.state['messages']),
                ),
                self.context.schemas,
            )
        key = hashlib.sha256(json.dumps({'message': parent, 'call': call}, sort_keys=True).encode()).hexdigest()
        token = ACTIVE_ACTION.set(key)
        try:
            if self.context.available_tools and call['name'] not in self._active_names(request.state) and not legacy:
                raise AppError('work_tool_not_loaded', '请先使用 load_tools 加载需要的工具组，再调用该工具', 409)
            result = await handler(request)
            if call['name'] == 'write_todos' and isinstance(result, Command):
                await self.context.invoke(call['name'], call['args'], key)
            return self._tool_update(call, result, legacy) if isinstance(result, ToolMessage) else result
        except AppError as error:
            if error.code in FATAL_TOOL_ERRORS or error.status_code in {401, 403} or 'uncertain' in error.code:
                raise
            return ToolMessage(
                content=json.dumps(
                    {'success': False, 'error': {'code': error.code, 'message': error.message}, 'data': None},
                    ensure_ascii=False,
                ),
                name=call['name'],
                tool_call_id=call['id'],
                status='error',
            )
        finally:
            if legacy:
                self.context.legacy_calls = self.context.legacy_calls - {
                    (str(parent), str(call['id']), str(call['name']))
                }
            ACTIVE_ACTION.reset(token)


def _tool(context: _ExecutionContext, name: str, description: str, fields: dict[str, Any]) -> StructuredTool:
    schema = create_model(name + 'Input', __config__=ConfigDict(extra='forbid'), **fields)

    async def invoke(**arguments: Any) -> Any:
        return await context.invoke(name, dict(arguments), ACTIVE_ACTION.get())

    return StructuredTool.from_function(coroutine=invoke, name=name, description=description, args_schema=schema)


def _tools(context: _ExecutionContext, code_enabled: bool, web_search_enabled: bool = False) -> list[StructuredTool]:
    context.allowed_toolsets = frozenset(WORK_TOOLSETS) if code_enabled else frozenset({'inspection'})
    required_id = (str, Field(min_length=1, description='当前任务中的文件编号'))
    values = [
        _tool(
            context,
            'load_tools',
            '按任务加载工具组：inspection 检查文件结构或完整历史；python 计算、制图和生成文件；shell 命令行程序；files 编辑沙盒文本和发布成果。加载不改变权限，不会启动沙盒。',
            {
                'groups': (
                    list[Literal['inspection', 'python', 'shell', 'files']]
                    if code_enabled
                    else list[Literal['inspection']],
                    Field(min_length=1, max_length=4),
                )
            },
        ),
        _tool(context, 'list_inputs', '查看当前任务的输入、成果版本及可用文件编号。', {}),
        _tool(
            context,
            'inspect_file',
            '仅需要文件格式、页数或工作表结构时使用；读取正文可直接调用 read_document。',
            {'file_id': required_id},
        ),
        _tool(
            context,
            'read_document',
            '读取任务文档，按预算返回全文或分页。has_more=true 时用 next、next_offset 续读，保留真实来源位置。',
            {
                'file_id': required_id,
                'start': (int, Field(default=1, ge=1)),
                'limit': (
                    int | None,
                    Field(default=None, ge=1, le=100000, description='可选的文本块数量；通常省略，按预算自动读取'),
                ),
                'offset': (int, Field(default=0, ge=0, description='段内字符续读位置，使用上次返回的 next_offset')),
            },
        ),
        _tool(
            context,
            'read_sheet',
            '分页读取当前任务表格，公式不在此工具中计算。',
            {
                'file_id': required_id,
                'sheet': (str, ''),
                'start_row': (int, Field(default=1, ge=1)),
                'limit': (int, Field(default=40, ge=1, le=100)),
            },
        ),
        _tool(
            context,
            'load_skill',
            '加载本任务已选技能的说明、文件目录和沙盒路径；需要执行时通过 run_python 或 execute 调用包内脚本。',
            {'name': (str, Field(min_length=1))},
        ),
        _tool(
            context,
            'request_input',
            '记录影响任务结果、必须由用户补充的信息。',
            {'question': (str, Field(min_length=1, max_length=2000))},
        ),
        _tool(
            context,
            'read_history',
            '按执行记录编号分页查看本任务的完整历史工具返回。',
            {
                'message_id': (str, Field(min_length=1)),
                'offset': (int, Field(default=0, ge=0)),
                'limit': (int, Field(default=2000, ge=1, le=3000)),
            },
        ),
    ]
    if web_search_enabled:
        values.append(
            _tool(
                context,
                'web_search',
                '搜索公开网页。按任务需要查询最新信息，返回标题、真实URL、摘要、网站和发布时间。摘要不是网页全文，引用时使用实际返回的链接。',
                {
                    'query': (str, Field(min_length=1, max_length=1000, description='必要的公开信息搜索关键词')),
                    'count': (int, Field(default=5, ge=1, le=10, strict=True)),
                    'freshness': (Literal['noLimit', 'oneDay', 'oneWeek', 'oneMonth', 'oneYear'], 'noLimit'),
                },
            )
        )
    if code_enabled:
        values.extend(
            [
                _tool(
                    context,
                    'run_python',
                    '在隔离沙盒运行 Python，计算、制图或生成文件。额外依赖用 packages 声明，成果保存到 outputs/。',
                    {
                        'code': (str, Field(min_length=1, max_length=40000)),
                        'packages': (list[str], Field(default_factory=list, max_length=20)),
                        'timeout': (int, Field(default=45, ge=1, le=120)),
                    },
                ),
                _tool(
                    context,
                    'execute',
                    '在隔离沙盒运行命令行程序；Python 代码优先使用 run_python。不能访问平台凭据和外部网络。',
                    {
                        'command': (str, Field(min_length=1, max_length=12000)),
                        'timeout': (int, Field(default=45, ge=1, le=120)),
                    },
                ),
                _tool(
                    context,
                    'write_file',
                    '写入 working/ 或 outputs/ 中的 UTF-8 文本。',
                    {'path': (str, Field(min_length=1)), 'content': (str, Field(max_length=100000))},
                ),
                _tool(
                    context,
                    'read_file',
                    '读取沙盒内最多 64KiB 文本；办公原件使用 read_document 或 read_sheet。',
                    {'path': (str, Field(min_length=1))},
                ),
                _tool(context, 'list_files', '查看当前沙盒工作区目录。', {'path': (str, '.')}),
                _tool(context, 'publish_outputs', '验证并发布 outputs/ 中的文件，获得固定版本和真实预览下载入口。', {}),
            ]
        )
    return values


def _summary_middleware(
    invoke_model: ModelInvoker, trigger_tokens: int, keep_tokens: int
) -> SummarizationMiddleware[Any, Any]:
    return SummarizationMiddleware(
        _WorkspaceModel(invoke_model, 'summary'),
        trigger=('tokens', trigger_tokens),
        keep=('tokens', keep_tokens),
        token_counter=_tokens,
        trim_tokens_to_summarize=None,
        summary_prompt='用中文压缩历史，保留任务目标、约束、文件编号、网页来源链接与日期、已确认结果、失败和下一步。只总结已有事实。\n\n{messages}',
    )


class _BudgetedSummaryMiddleware(AgentMiddleware[Any, Any, Any]):
    """Keep official summary behavior while accounting for non-message input."""

    def __init__(
        self,
        invoke: ModelInvoker,
        execution: _ExecutionMiddleware,
        input_budget: int | None,
        trigger: int,
        keep: int,
    ) -> None:
        super().__init__()
        self.invoke, self.execution = invoke, execution
        self.input_budget, self.trigger, self.keep = input_budget, trigger, keep

    @property
    def name(self) -> str:
        # Preserve node identities in existing durable workspace checkpoints.
        return 'SummarizationMiddleware'

    async def abefore_model(self, state: WorkspaceState, runtime: 'Runtime[Any]') -> dict[str, Any] | None:
        self.execution.select_tools(state)
        trigger, keep = self.trigger, self.keep
        if self.input_budget is not None:
            prefix = _tokens([SystemMessage(content=self.execution._system(state))])
            schema = estimate_tool_schemas(self.execution.context.schemas)
            available = max(1, self.input_budget - prefix - schema - 1024)
            trigger = min(trigger, max(2, available * 7 // 10))
            keep = min(keep, max(1, trigger // 3))
        result = await _summary_middleware(self.invoke, trigger, keep).abefore_model(state, runtime)
        if state.get('toolsets_revision') != 1:
            result = {
                **(result or {}),
                'loaded_toolsets': {
                    group: state.get('loaded_toolsets', {}).get(group) is True for group in WORK_TOOLSETS
                },
                'toolsets_revision': 1,
            }
        return result


def _broken_summary(message: BaseMessage) -> bool:
    return (
        isinstance(message, HumanMessage)
        and message.additional_kwargs.get('lc_source') == 'summarization'
        and not valid_work_summary(_text(message))
    )


def _state_messages(state: 'StateSnapshot') -> list[BaseMessage]:
    messages = state.values.get('messages', [])
    if not isinstance(messages, list) or any(not isinstance(message, BaseMessage) for message in messages):
        raise AppError(
            'work_summary_context_unavailable', '历史执行上下文不完整，原始资料和记录已保留，请重新加入资料后继续', 409
        )
    return cast('list[BaseMessage]', messages)


async def _restore_summary_history(
    graph: 'CompiledStateGraph[Any, Any, Any, Any]',
    config: 'RunnableConfig',
    saved: 'StateSnapshot',
) -> list[BaseMessage] | None:
    messages = _state_messages(saved)
    if not any(_broken_summary(message) for message in messages):
        return None
    segments = [messages]
    visited: set[str] = set()
    for _ in range(256):
        parent = saved.parent_config
        parent_values = parent.get('configurable', {}) if parent else {}
        identity = parent_values.get('checkpoint_id')
        if (
            not parent
            or parent_values.get('thread_id') != config.get('configurable', {}).get('thread_id')
            or not isinstance(identity, str)
            or identity in visited
        ):
            break
        visited.add(identity)
        saved = await graph.aget_state(parent)
        messages = _state_messages(saved)
        if not messages:
            break
        segments.append(messages)
        if any(_broken_summary(message) for message in messages):
            continue
        restored: dict[str, BaseMessage] = {}
        for segment in reversed(segments):
            for message in segment:
                if _broken_summary(message):
                    continue
                if not message.id or isinstance(message, RemoveMessage):
                    raise AppError(
                        'work_summary_context_unavailable',
                        '历史消息标识不完整，原始资料和记录已保留，请重新加入资料后继续',
                        409,
                    )
                restored[message.id] = message
        return list(restored.values())
    raise AppError(
        'work_summary_context_unavailable',
        '无法恢复缺失的历史摘要，原始资料和执行记录仍保留，请重新读取资料后继续',
        409,
    )


class WorkspaceKernel:
    def __init__(self, database_url: str) -> None:
        self.checkpoints = CheckpointStore(database_url, 'work_checkpoints')

    async def run(  # noqa: PLR0913 - accepted task settings and native execution callbacks form one boundary
        self,
        *,
        task_id: UUID,
        checkpoint_id: str | None,
        message: str | None,
        message_id: UUID,
        system_prompt: str,
        skill_texts: dict[str, str],
        code_enabled: bool,
        invoke_model: ModelInvoker,
        invoke_tool: ToolInvoker,
        write_progress: ProgressWriter,
        write_checkpoint: CheckpointWriter,
        input_budget: int | None,
        trigger_tokens: int,
        keep_tokens: int,
        web_search_enabled: bool = False,
        write_tool_context: ToolContextWriter | None = None,
        execution_instructions: str = '',
        tool_available: ToolAvailability | None = None,
    ) -> AgentKernelOutcome:
        planning = TodoListMiddleware(
            system_prompt='复杂任务先维护任务清单；用真实工具结果核对进度。简单问答直接回答。不要提前标记完成。'
        )
        context = _ExecutionContext(
            invoke_tool, skill_texts, write_tool_context, system_prompt + '\n' + planning.system_prompt
        )
        context.execution_instructions = execution_instructions
        context.tool_available = tool_available
        tools = _tools(context, code_enabled, web_search_enabled)
        context.available_tools = (*tools, *planning.tools)
        model = _WorkspaceModel(invoke_model)
        execution = _ExecutionMiddleware(context)
        middleware: list[Any] = [
            _BudgetedSummaryMiddleware(invoke_model, execution, input_budget, trigger_tokens, keep_tokens),
            planning,
            execution,
        ]
        async with self.checkpoints.saver() as saver:
            graph = create_agent(
                model,
                tools=tools,
                state_schema=WorkspaceState,
                checkpointer=saver,
                middleware=middleware,
                system_prompt=system_prompt,
            )
            config = self.checkpoints.config(task_id, checkpoint_id)
            saved = await graph.aget_state(config)
            if message is None and saved.next and saved.values.get('toolsets_revision') != 1:
                saved_messages = _state_messages(saved)
                resolved = {item.tool_call_id for item in saved_messages if isinstance(item, ToolMessage)}
                context.legacy_calls = frozenset(
                    (str(item.id), str(call['id']), str(call['name']))
                    for item in saved_messages
                    if isinstance(item, AIMessage) and item.id
                    for call in item.tool_calls
                    if call['id'] not in resolved and call['name'] in {tool.name for tool in tools} - WORK_CORE_TOOLS
                )
            execution.select_tools(saved.values or {})
            initial: dict[str, Any] | None = (
                None
                if message is None
                else {
                    'messages': [HumanMessage(id=str(message_id), content=message)],
                    'todos': [],
                    'awaiting_input': False,
                    'reviewed': False,
                    'review_note': '',
                    'loaded_toolsets': dict.fromkeys(WORK_TOOLSETS, False),
                    'toolsets_revision': 1,
                }
            )
            latest: dict[str, Any] = dict(saved.values or {})
            if message is None and not saved.next:
                initial = {
                    'messages': [
                        HumanMessage(
                            id=str(message_id),
                            content='请核对最近一次用户要求、已保存的执行记录与成果；先处理尚未解决的失败，再继续推进任务。未实际完成的事项不能宣称成功。',
                        )
                    ],
                    'awaiting_input': False,
                    'reviewed': False,
                    'review_note': '',
                }
            restored = await _restore_summary_history(graph, config, saved)
            if restored is not None:
                initial = {
                    **(initial or {}),
                    'messages': [
                        RemoveMessage(id=REMOVE_ALL_MESSAGES),
                        *restored,
                        *((initial or {}).get('messages', [])),
                    ],
                    'awaiting_input': False,
                    'reviewed': False,
                    'review_note': '',
                }
            latest_id = checkpoint_id
            with tracing_context(enabled=False):
                async for mode, data in graph.astream(
                    cast('InputAgentState | None', initial), config=config, stream_mode=['updates', 'checkpoints']
                ):
                    if mode != 'checkpoints' or not isinstance(data, dict):
                        continue
                    values = data.get('values')
                    checkpoint_config = data.get('config')
                    if not isinstance(values, dict) or not isinstance(checkpoint_config, dict):
                        continue
                    latest = values
                    plan = tuple(cast('dict[str, object]', item) for item in values.get('todos', []))
                    await write_progress(plan)
                    messages = values.get('messages', [])
                    if messages and (
                        isinstance(messages[-1], ToolMessage)
                        or (isinstance(messages[-1], AIMessage) and not messages[-1].tool_calls)
                    ):
                        latest_id = self.checkpoints.result_checkpoint_id(cast('RunnableConfig', checkpoint_config))
                        await write_checkpoint(latest_id)
            messages = latest.get('messages', [])
            text = _text(messages[-1]) if messages else ''
            return AgentKernelOutcome(
                text,
                latest_id,
                bool(latest.get('awaiting_input')),
                tuple(cast('dict[str, object]', item) for item in latest.get('todos', [])),
            )
