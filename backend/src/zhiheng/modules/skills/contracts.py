from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from zhiheng.modules.skills.validation import MAX_SLUG_LENGTH, SLUG_PATTERN

SkillSlug = Annotated[str, StringConstraints(min_length=1, max_length=MAX_SLUG_LENGTH, pattern=SLUG_PATTERN)]
SkillStatus = Literal['enabled', 'disabled']
SkillImportAction = Literal['install', 'update', 'restore', 'unchanged']


@dataclass(frozen=True)
class SkillAccess:
    id: UUID
    owner_id: UUID
    version: int
    status: str


class SkillInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    slug: str
    name: str
    description: str
    package_version: str
    version: int
    status: SkillStatus
    source: Literal['builtin', 'upload']
    revision_id: UUID
    content_hash: str
    file_count: int
    total_bytes: int
    created_at: datetime
    updated_at: datetime


class DraftInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    draft_id: UUID
    expires_at: datetime
    slug: str
    name: str
    description: str
    package_version: str
    content_hash: str
    file_count: int
    total_bytes: int
    action: SkillImportAction = 'install'
    target_skill_id: UUID | None = None
    expected_version: int | None = None
    current_package_version: str | None = None


class SkillPage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[SkillInfo]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True)
class SkillRevisionValue:
    skill_id: UUID
    revision_id: UUID
    slug: str
    name: str
    content_hash: str
    content: str


@dataclass(frozen=True)
class SkillSelection:
    revisions: tuple[SkillRevisionValue, ...] = ()
