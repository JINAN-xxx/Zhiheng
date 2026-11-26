"""Public values for goal-driven workspace tasks and their observable execution facts."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from zhiheng.modules.work.base import Input, Version, safe_text
from zhiheng.modules.work.facts import visible_work_text

MAX_PLAN_DISPLAY_CHARS = 2000

ExecutionState = Literal['idle', 'queued', 'running', 'waiting_input', 'paused', 'completed', 'failed', 'cancelled']
PlanStatus = Literal['pending', 'in_progress', 'completed']
StepStatus = Literal['pending', 'running', 'succeeded', 'completed', 'failed', 'unknown', 'cancelled']


class AgentTaskConfiguration(Input):
    model_id: UUID
    skill_slugs: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=5)

    @field_validator('skill_slugs')
    @classmethod
    def unique_skills(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError('技能不能重复')
        return value


class CreateAgentTask(AgentTaskConfiguration):
    goal: Annotated[str, Field(max_length=32000)] = ''
    file_ids: list[UUID] = Field(default_factory=list, max_length=20)
    client_request_id: UUID

    @field_validator('goal')
    @classmethod
    def text(cls, value: str) -> str:
        return safe_text(value).strip()

    @field_validator('file_ids')
    @classmethod
    def unique_files(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError('文件不能重复')
        return value


class UpdateAgentTask(Input):
    expected_version: Version
    title: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    goal: Annotated[str, Field(min_length=1, max_length=32000)] | None = None
    status: Literal['active', 'completed', 'archived'] | None = None
    model_id: UUID | None = None
    skill_slugs: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(default=None, max_length=5)

    @model_validator(mode='after')
    def values_present(self) -> 'UpdateAgentTask':
        for key in self.model_fields_set - {'expected_version'}:
            if getattr(self, key) is None:
                raise ValueError('字段不能为 null')
        for key in ('title', 'goal'):
            value = getattr(self, key)
            if value is not None:
                value = safe_text(value).strip()
                if not value:
                    raise ValueError('文本不能为空')
                setattr(self, key, value)
        if self.skill_slugs is not None and len(self.skill_slugs) != len(set(self.skill_slugs)):
            raise ValueError('技能不能重复')
        return self


class AgentTurn(Input):
    client_request_id: UUID
    expected_version: Version
    message: Annotated[str, Field(max_length=32000)] = ''
    file_ids: list[UUID] = Field(default_factory=list, max_length=20)
    resume: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def valid_turn(self) -> 'AgentTurn':
        self.message = safe_text(self.message).strip()
        if self.resume and self.message:
            raise ValueError('继续执行时不应重复发送消息')
        if not self.resume and not self.message:
            raise ValueError('请输入本次要求')
        if len(self.file_ids) != len(set(self.file_ids)):
            raise ValueError('文件不能重复')
        return self


class WorkPlanItem(Input):
    content: Annotated[str, Field(min_length=1, max_length=MAX_PLAN_DISPLAY_CHARS)]
    status: PlanStatus

    @field_validator('content', mode='before')
    @classmethod
    def safe_display(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        visible = visible_work_text(value)
        return visible[: MAX_PLAN_DISPLAY_CHARS - 1] + '…' if len(visible) > MAX_PLAN_DISPLAY_CHARS else visible


class WorkInputInfo(Input):
    file_id: UUID
    name: str
    version: int
    size: int
    mime: str
    sha256: str
    file_version: int
    acl_version: int
    created_at: datetime


class WorkStepInfo(Input):
    id: UUID
    run_id: UUID
    action_id: str
    kind: str
    name: str
    status: StepStatus
    summary: str
    details: dict[str, object]
    result: dict[str, object] | None
    created_at: datetime
    updated_at: datetime


class WorkArtifactInfo(Input):
    id: UUID
    run_id: UUID
    file_id: UUID
    name: str
    logical_path: str
    version: int
    size: int
    mime: str
    sha256: str
    validation: dict[str, object]
    preview_file_id: UUID | None
    created_at: datetime
    url: str
    preview_url: str | None


class WorkSandboxInfo(Input):
    id: UUID
    task_id: UUID
    run_id: UUID
    attempt_id: UUID
    sandbox_id: str | None
    image: str
    status: str
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    error: str | None


class WorkWorkspaceState(Input):
    sequence: int = Field(ge=0, strict=True)
    files: list[dict[str, object]]
    failed_operations: list[str]
    run_id: UUID


class WorkModelOption(Input):
    id: UUID
    name: str
    max_output_tokens: int


class WorkSkillOption(Input):
    slug: str
    name: str
    description: str
    version: int


class WorkOptions(Input):
    models: list[WorkModelOption]
    skills: list[WorkSkillOption]
    can_execute_code: bool
    sandbox_available: bool
