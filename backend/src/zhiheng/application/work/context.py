"""Shared platform services and immutable policy used by workspace execution."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

from zhiheng.application.execution.admission import RunAdmission
from zhiheng.application.files.inputs import FileInputService
from zhiheng.application.files.objects import FileObjects
from zhiheng.application.files.queries import FileQueries
from zhiheng.infrastructure.database import Database
from zhiheng.infrastructure.sandbox import SandboxFactory
from zhiheng.modules.billing.public import UsageService
from zhiheng.modules.files.public import FileService, WorkspaceReader
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.models.public import ModelService, SummaryPolicy
from zhiheng.modules.search.public import WebSearch
from zhiheng.modules.skills.public import SkillService
from zhiheng.modules.work.public import WorkAgentStore, WorkspaceAgentKernel
from zhiheng.runtime.runs import RunStore


@dataclass(frozen=True)
class WorkspacePolicy:
    run_seconds: int = 600
    model_calls: int = 16
    tool_calls: int = 24
    max_input_bytes: int = 100 * 1024 * 1024
    input_tokens: int | None = None
    summary: SummaryPolicy = field(default_factory=SummaryPolicy)
    sandbox_image: str = ''


@dataclass
class WorkAgentContext:
    database: Database
    identity: IdentityService
    models: ModelService
    skills: SkillService
    usage: UsageService
    runs: RunStore
    admission: RunAdmission
    files: FileService
    objects: FileObjects
    queries: FileQueries
    inputs: FileInputService
    tasks: WorkAgentStore
    kernel: WorkspaceAgentKernel
    sandbox: SandboxFactory
    reader: WorkspaceReader
    policy: WorkspacePolicy
    model_executor: Callable[[UUID], Awaitable[None]] | None = None
    checkpoint_cleanup: Callable[[str], Awaitable[None]] | None = None
    web_search: WebSearch | None = None
