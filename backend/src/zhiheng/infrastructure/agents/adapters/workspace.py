"""Official task Agent, tool middleware and durable checkpoints over native model calls."""

import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, Sequence
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
from langgraph.types import Command
from langsmith import tracing_context
from pydantic import ConfigDict, Field, PrivateAttr, create_model

from zhiheng.infrastructure.agents.adapters.checkpoints import CheckpointStore
from zhiheng.modules.models.public import AgentModelMessage, ModelMessage, ModelToolCall, estimate_messages
from zhiheng.modules.work.public import (
    AgentKernelOutcome,
    CheckpointWriter,
    ModelInvoker,
    ProgressWriter,
    ToolInvoker,
)
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from langchain.agents.middleware.types import InputAgentState, ModelRequest, ModelResponse, ToolCallRequest
    from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
    from langchain_core.messages import MessageLikeRepresentation
    from langchain_core.runnables import RunnableConfig

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


class WorkspaceState(PlanningState, total=False):
    loaded_skills: Annotated[list[str], _merge_skills]
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


class _ExecutionMiddleware(AgentMiddleware[Any, Any, Any]):
    def __init__(self, context: _ExecutionContext) -> None:
        super().__init__()
        self.context = context

    async def awrap_model_call(
        self,
        request: 'ModelRequest[Any]',
        handler: Callable[['ModelRequest[Any]'], Awaitable['ModelResponse[Any]']],
    ) -> 'ModelResponse[Any]':
        names = request.state.get('loaded_skills', [])
        instructions = (
            '\n\n'.join(
                self.context.skill_texts[name]
                for name in names
                if isinstance(name, str) and name in self.context.skill_texts
            )
            if isinstance(names, list)
            else ''
        )
        note = str(request.state.get('review_note', ''))
        if instructions or note:
            system = request.system_message.content if request.system_message else ''
            request = request.override(
                system_message=SystemMessage(content=str(system) + '\n\n已加载技能：\n' + instructions + '\n' + note)
            )
        return await handler(request)

    @hook_config(can_jump_to=['model'])
    def after_model(self, state: WorkspaceState, runtime: Any) -> dict[str, Any] | None:
        if state.get('reviewed') or state.get('awaiting_input') or not state.get('todos'):
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

    async def awrap_tool_call(
        self,
        request: 'ToolCallRequest',
        handler: Callable[['ToolCallRequest'], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        call = request.tool_call
        parent = next(
            (message.id for message in reversed(request.state['messages']) if isinstance(message, AIMessage)), ''
        )
        key = hashlib.sha256(json.dumps({'message': parent, 'call': call}, sort_keys=True).encode()).hexdigest()
        token = ACTIVE_ACTION.set(key)
        try:
            result = await handler(request)
            if call['name'] == 'write_todos' and isinstance(result, Command):
                await self.context.invoke(call['name'], call['args'], key)
            if isinstance(result, ToolMessage):
                try:
                    envelope = json.loads(str(result.content))
                except ValueError:
                    envelope = None
                if isinstance(envelope, dict) and envelope.get('success') is False:
                    result = result.model_copy(update={'status': 'error'})
                if call['name'] == 'load_skill' and result.status != 'error':
                    return Command(update={'messages': [result], 'loaded_skills': [str(call['args'].get('name', ''))]})
                if call['name'] == 'request_input' and result.status != 'error':
                    return Command(update={'messages': [result], 'awaiting_input': True})
            return result
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
            ACTIVE_ACTION.reset(token)


def _tool(context: _ExecutionContext, name: str, description: str, fields: dict[str, Any]) -> StructuredTool:
    schema = create_model(name + 'Input', __config__=ConfigDict(extra='forbid'), **fields)

    async def invoke(**arguments: Any) -> Any:
        return await context.invoke(name, dict(arguments), ACTIVE_ACTION.get())

    return StructuredTool.from_function(coroutine=invoke, name=name, description=description, args_schema=schema)


def _tools(context: _ExecutionContext, code_enabled: bool, web_search_enabled: bool = False) -> list[StructuredTool]:
    required_id = (str, Field(min_length=1, description='当前任务中的文件编号'))
    values = [
        _tool(context, 'list_inputs', '查看当前任务的输入、成果版本及可用文件编号。', {}),
        _tool(context, 'inspect_file', '识别当前任务文件的格式、页数或工作表结构。', {'file_id': required_id}),
        _tool(
            context,
            'read_document',
            '分页读取当前任务文档，返回真实来源。',
            {
                'file_id': required_id,
                'start': (int, Field(default=1, ge=1)),
                'limit': (int, Field(default=40, ge=1, le=60)),
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
            context, 'load_skill', '按名称加载本任务已选技能的方法和操作要求。', {'name': (str, Field(min_length=1))}
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
                    '在隔离沙盒运行 Python。平台默认镜像预装 NumPy、Pandas、OpenCV headless（cv2）、Matplotlib 和 Pillow；无需先安装或探测。额外依赖用 packages 声明；OpenCV 包名为 opencv-python-headless。没有图形桌面，成果保存到 outputs/。',
                    {
                        'code': (str, Field(min_length=1, max_length=40000)),
                        'packages': (list[str], Field(default_factory=list, max_length=20)),
                        'timeout': (int, Field(default=45, ge=1, le=120)),
                    },
                ),
                _tool(
                    context,
                    'execute',
                    '在当前隔离沙盒执行命令，不访问平台凭据和外部网络。',
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
                _tool(context, 'read_file', '读取沙盒工作区内最多 64KiB 文本。', {'path': (str, Field(min_length=1))}),
                _tool(context, 'list_files', '查看当前沙盒工作区目录。', {'path': (str, '.')}),
                _tool(context, 'publish_outputs', '验证并发布 outputs/ 中的文件，获得固定版本和真实预览下载入口。', {}),
            ]
        )
    return values


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
    ) -> AgentKernelOutcome:
        context = _ExecutionContext(invoke_tool, skill_texts)
        model = _WorkspaceModel(invoke_model)
        middleware: list[Any] = [
            SummarizationMiddleware(
                _WorkspaceModel(invoke_model, 'summary'),
                trigger=('tokens', trigger_tokens),
                keep=('tokens', keep_tokens),
                token_counter=_tokens,
                summary_prompt='用中文压缩历史，保留任务目标、约束、文件编号、网页来源链接与日期、已确认结果、失败和下一步。只总结已有事实。\n\n{messages}',
            ),
            TodoListMiddleware(
                system_prompt='复杂任务先维护任务清单；用真实工具结果核对进度。简单问答直接回答。不要提前标记完成。'
            ),
            _ExecutionMiddleware(context),
        ]
        async with self.checkpoints.saver() as saver:
            graph = create_agent(
                model,
                tools=_tools(context, code_enabled, web_search_enabled),
                state_schema=WorkspaceState,
                checkpointer=saver,
                middleware=middleware,
                system_prompt=system_prompt,
            )
            config = self.checkpoints.config(task_id, checkpoint_id)
            saved = await graph.aget_state(config)
            initial: dict[str, Any] | None = (
                None
                if message is None
                else {
                    'messages': [HumanMessage(id=str(message_id), content=message)],
                    'todos': [],
                    'awaiting_input': False,
                    'reviewed': False,
                    'review_note': '',
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
