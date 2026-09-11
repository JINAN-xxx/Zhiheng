from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from pydantic.alias_generators import to_camel

from plugins.agent_extension.entity.vo.agent_vo import AgentId
from plugins.agent_extension.entity.vo.skill_vo import QueryInteger

WorkTaskStatus = Literal['active', 'completed', 'archived']
WorkMessageRole = Literal['user', 'assistant']
WorkRunStatus = Literal['pending', 'streaming', 'completed', 'cancelled', 'interrupted', 'failed']
BoundedIdentifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=36)]
ClientMessageId = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100),
]
WorkTitle = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100),
]
WorkContent = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=32000),
]


class _WorkRequest(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        extra='forbid',
        strict=True,
    )


class _WorkResponse(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        from_attributes=True,
        extra='ignore',
    )


class WorkTaskCreateModel(_WorkRequest):
    goal: WorkContent
    agent_ids: list[AgentId] = Field(min_length=1, max_length=10)
    active_agent_id: AgentId
    client_message_id: ClientMessageId

    @field_validator('agent_ids')
    @classmethod
    def reject_duplicate_agents(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError('Agent IDs must not contain duplicates')
        return value

    @model_validator(mode='after')
    def require_active_agent_member(self) -> 'WorkTaskCreateModel':
        if self.active_agent_id not in self.agent_ids:
            raise ValueError('Active agent must be included in agentIds')
        return self


class WorkTaskRunRequestModel(_WorkRequest):
    agent_id: AgentId
    client_message_id: ClientMessageId
    message: WorkContent


class WorkTaskUpdateModel(_WorkRequest):
    expected_version: int = Field(ge=1)
    title: WorkTitle | None = None
    goal: WorkContent | None = None
    status: WorkTaskStatus | None = None

    @model_validator(mode='after')
    def require_change(self) -> 'WorkTaskUpdateModel':
        changed_fields = {'title', 'goal', 'status'} & self.model_fields_set
        if not changed_fields:
            raise ValueError('At least one task field must be changed')
        if any(getattr(self, field) is None for field in changed_fields):
            raise ValueError('Changed task fields must not be null')
        return self


class WorkTaskPageQueryModel(_WorkRequest):
    keyword: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=100)] = ''
    status: WorkTaskStatus | None = None
    page_num: QueryInteger = Field(default=1, ge=1)
    page_size: QueryInteger = Field(default=20, ge=1, le=100)


class WorkTaskMemberAddModel(_WorkRequest):
    expected_version: int = Field(ge=1)
    agent_id: AgentId


class WorkTaskMemberUpdateModel(_WorkRequest):
    expected_version: int = Field(ge=1)
    expected_published_version: int = Field(ge=1)


class WorkTaskCancelModel(_WorkRequest):
    run_id: BoundedIdentifier


class WorkTaskMemberModel(_WorkResponse):
    task_id: BoundedIdentifier
    agent_id: BoundedIdentifier
    agent_name_snapshot: str
    published_version: int
    available: bool = True
    latest_published_version: int | None = None
    added_at: datetime | None = None
    removed_at: datetime | None = None


class WorkTaskMessageModel(_WorkResponse):
    message_id: BoundedIdentifier
    task_id: BoundedIdentifier
    client_message_id: str | None = None
    reply_to_message_id: BoundedIdentifier | None = None
    role: WorkMessageRole
    content: str
    agent_id: BoundedIdentifier | None = None
    agent_name_snapshot: str | None = None
    published_version: int | None = None
    reasoning_content: str = ''
    run_id: BoundedIdentifier | None = None
    run_status: WorkRunStatus | None = None
    usage_snapshot: dict[str, object] | None = None
    error_snapshot: dict[str, object] | None = None
    created_at: datetime | None = None


class WorkTaskModel(_WorkResponse):
    task_id: BoundedIdentifier
    owner_user_id: int
    title: str
    goal: str
    status: WorkTaskStatus
    active_agent_id: BoundedIdentifier | None = None
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class WorkTaskDetailModel(WorkTaskModel):
    members: list[WorkTaskMemberModel] = Field(default_factory=list)
    messages: list[WorkTaskMessageModel] = Field(default_factory=list)


class WorkAgentOptionModel(_WorkResponse):
    agent_id: BoundedIdentifier
    name: str
    description: str
    status: Literal['published']
    published_version: int
