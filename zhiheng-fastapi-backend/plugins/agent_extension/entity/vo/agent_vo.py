from datetime import datetime
from typing import Annotated, Literal
from uuid import RFC_4122, UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic.alias_generators import to_camel

from plugins.agent_extension.entity.vo.skill_vo import QueryInteger, SkillSlug
from plugins.ai.entity.vo.ai_developer_api_vo import ApplicationResourceId
from plugins.ai.entity.vo.ai_model_catalog_vo import ModelResourceId

UUID4_VERSION = 4


def _validate_agent_id(value: str) -> str:
    parsed = UUID(value)
    if parsed.version != UUID4_VERSION or parsed.variant != RFC_4122 or str(parsed) != value:
        raise ValueError('Agent ID must be a canonical UUID4')
    return value


AgentId = Annotated[str, StringConstraints(strict=True), AfterValidator(_validate_agent_id)]
KnowledgeBaseId = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]


class _RequestModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, validate_by_alias=True, validate_by_name=False, extra='forbid')


class AgentConfigModel(_RequestModel):
    name: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]
    description: Annotated[str, StringConstraints(strict=True, max_length=500)] = ''
    model_id: ModelResourceId
    system_prompt: Annotated[str, StringConstraints(strict=True, max_length=32000)] = ''
    welcome_message: Annotated[str, StringConstraints(strict=True, max_length=2000)] = ''
    skill_slugs: list[SkillSlug] = Field(default_factory=list, max_length=20, strict=True)
    knowledge_base_ids: list[KnowledgeBaseId] = Field(default_factory=list, max_length=20, strict=True)
    application_id: ApplicationResourceId | None = None
    temperature: float = Field(default=0.7, ge=0, le=2, strict=True, allow_inf_nan=False)
    max_tokens: int = Field(default=2048, ge=1, le=131072, strict=True)

    @field_validator('skill_slugs', 'knowledge_base_ids')
    @classmethod
    def unique_selections(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError('Selections must not contain duplicates')
        return value


class AgentUpdateModel(AgentConfigModel):
    expected_version: int = Field(strict=True, ge=1)


class AgentPublishModel(_RequestModel):
    expected_version: int = Field(strict=True, ge=1)


class AgentPageQueryModel(_RequestModel):
    keyword: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=100)] = ''
    page_num: QueryInteger = Field(default=1, ge=1)
    page_size: QueryInteger = Field(default=12, ge=1, le=100)


class AgentConfigResponseModel(AgentConfigModel):
    model_config = ConfigDict(validate_by_name=True, from_attributes=True, extra='ignore')


class AgentDetailModel(AgentConfigResponseModel):
    agent_id: AgentId
    owner_user_id: int
    version: int
    status: Literal['draft', 'published']
    published_config: AgentConfigResponseModel | None = None
    published_version: int | None = None
    published_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class AgentPublishedModel(AgentConfigResponseModel):
    agent_id: AgentId
    owner_user_id: int
    published_version: int
