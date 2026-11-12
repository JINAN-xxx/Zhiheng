"""skills resource wire DTOs."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from zhiheng.modules.skills.public import ResourceSkillInfo, RevisionInfo, SkillStatus


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file_id: UUID


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: SkillStatus


class SkillPage(BaseModel):
    items: list[ResourceSkillInfo]
    next_cursor: str | None
    has_more: bool


class RevisionPage(BaseModel):
    items: list[RevisionInfo]
    next_cursor: str | None
    has_more: bool
