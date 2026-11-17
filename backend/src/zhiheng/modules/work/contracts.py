from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from zhiheng.modules.agents.public import AgentConfig
from zhiheng.modules.knowledge.public import Hit
from zhiheng.modules.work.agent_contracts import (
    AgentTaskConfiguration,
    ExecutionState,
    WorkArtifactInfo,
    WorkInputInfo,
    WorkPlanItem,
    WorkStepInfo,
)
from zhiheng.modules.work.base import Input, Version, safe_text

TaskStatus = Literal['active', 'completed', 'archived']


class CreateTask(Input):
    goal: Annotated[str, Field(min_length=1, max_length=32000)]
    agent_ids: Annotated[list[UUID], Field(min_length=1, max_length=10)]
    active_agent_id: UUID
    client_request_id: UUID

    @field_validator('goal')
    @classmethod
    def text(cls, value: str) -> str:
        value = safe_text(value).strip()
        if not value:
            raise ValueError('目标不能为空')
        return value

    @model_validator(mode='after')
    def members(self) -> 'CreateTask':
        if len(set(self.agent_ids)) != len(self.agent_ids) or self.active_agent_id not in self.agent_ids:
            raise ValueError('成员重复或未包含所选智能体')
        return self


class UpdateTask(Input):
    expected_version: Version
    title: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    goal: Annotated[str, Field(min_length=1, max_length=32000)] | None = None
    status: TaskStatus | None = None
    active_agent_id: UUID | None = None

    @field_validator('title', 'goal')
    @classmethod
    def text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = safe_text(value).strip()
        if not value:
            raise ValueError('文本不能为空')
        return value

    @model_validator(mode='after')
    def nonnull(self) -> 'UpdateTask':
        if any(getattr(self, key) is None for key in self.model_fields_set & {'title', 'goal', 'status'}):
            raise ValueError('字段不能为 null')
        return self


class AddMember(Input):
    agent_id: UUID
    expected_version: Version


class UpgradeMember(Input):
    expected_version: Version
    expected_agent_revision: Version


class WorkTurn(Input):
    agent_id: UUID
    client_request_id: UUID
    message: Annotated[str, Field(min_length=1, max_length=32000)]
    expected_version: Version

    @field_validator('message')
    @classmethod
    def text(cls, value: str) -> str:
        return CreateTask.text(value)


class TaskSummary(Input):
    id: UUID
    owner_id: UUID
    title: str
    goal: str
    status: TaskStatus
    mode: Literal['legacy', 'agent'] = 'legacy'
    model_id: UUID | None = None
    execution_state: ExecutionState = 'idle'
    version: int
    current_run_id: UUID | None
    preparation_id: UUID | None = None
    active_agent_id: UUID | None
    create_client_request_id: UUID
    created_at: datetime
    updated_at: datetime


class MemberInfo(Input):
    agent_id: UUID
    agent_name_snapshot: str
    agent_revision: int
    latest_agent_revision: int | None = None
    available: bool = False
    removed_at: datetime | None = None


class MessageInfo(Input):
    id: UUID
    client_request_id: UUID
    role: Literal['user', 'assistant']
    content: str
    knowledge_sources: list[Hit] = Field(default_factory=list)
    reasoning: str
    run_id: UUID | None
    status: str
    usage: dict[str, object] | None
    error: dict[str, object] | None
    agent_id: UUID | None
    agent_name_snapshot: str | None
    agent_revision: int | None
    sequence: int
    created_at: datetime


class TaskDetail(TaskSummary):
    members: list[MemberInfo]
    messages: list[MessageInfo]
    configuration: AgentTaskConfiguration | None = None
    inputs: list[WorkInputInfo] = Field(default_factory=list)
    steps: list[WorkStepInfo] = Field(default_factory=list)
    artifacts: list[WorkArtifactInfo] = Field(default_factory=list)
    plan: list[WorkPlanItem] = Field(default_factory=list)


class TaskPage(Input):
    items: list[TaskSummary]
    total: int
    page: int
    page_size: int


class WorkAgentConfig(AgentConfig):
    """Work retains its bounded input policy and accepted member settings."""

    history_rounds: int = Field(default=20, strict=True, ge=0, le=20)
