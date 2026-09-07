from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

from plugins.agent_extension.domain.skill_id import validate_skill_id

SkillStatus = Literal['enabled', 'disabled']
SkillSourceType = Literal['builtin', 'upload']
SkillId = Annotated[str, AfterValidator(validate_skill_id)]
SkillSlug = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=100, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$'),
]


def _parse_query_integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError('分页参数必须是整数')
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ValueError('分页参数必须是整数')


QueryInteger = Annotated[int, BeforeValidator(_parse_query_integer)]


class _RequestModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, validate_by_alias=True, validate_by_name=False, extra='forbid')


class _ResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        from_attributes=True,
        extra='ignore',
    )


class SkillPageQueryModel(_RequestModel):
    keyword: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, max_length=100)] = ''
    status: SkillStatus | None = None
    page_num: QueryInteger = Field(default=1, ge=1)
    page_size: QueryInteger = Field(default=12, ge=1, le=100)


class SkillStatusUpdateModel(_RequestModel):
    status: SkillStatus
    expected_version: int = Field(strict=True, ge=1)


class SkillModel(_ResponseModel):
    skill_id: SkillId
    skill_slug: SkillSlug
    skill_name: str
    description: str
    version: str
    status: SkillStatus
    source_type: SkillSourceType
    lock_version: int
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


class SkillDetailModel(SkillModel):
    content_hash: str
    metadata_json: dict[str, object] = Field(default_factory=dict)


class AvailableSkillModel(_ResponseModel):
    skill_slug: SkillSlug
    skill_name: str
    description: str

