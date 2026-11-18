"""SDK-independent interfaces between task execution and its Agent adapter."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from zhiheng.modules.models.public import AgentModelMessage, ModelToolCall


@dataclass(frozen=True)
class ModelTurn:
    run_id: UUID
    content: str
    tool_calls: tuple[ModelToolCall, ...] = ()
    reasoning: str = ''


@dataclass(frozen=True)
class AgentKernelOutcome:
    text: str
    checkpoint_id: str | None
    waiting_input: bool
    plan: tuple[dict[str, object], ...]


ModelInvoker = Callable[[tuple[AgentModelMessage, ...], tuple[dict[str, object], ...], str, str], Awaitable[ModelTurn]]
ToolInvoker = Callable[[str, dict[str, object], str], Awaitable[dict[str, object]]]
ProgressWriter = Callable[[tuple[dict[str, object], ...]], Awaitable[None]]
CheckpointWriter = Callable[[str], Awaitable[None]]


class WorkspaceAgentKernel(Protocol):
    async def run(  # noqa: PLR0913 - immutable task settings and native execution callbacks
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
    ) -> AgentKernelOutcome: ...
