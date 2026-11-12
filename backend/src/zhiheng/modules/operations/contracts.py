from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Version = Annotated[StrictInt, Field(ge=1)]
NoticeKind = Literal['notice', 'announcement']
NoticeStatus = Literal['draft', 'published']
LogKind = Literal['login', 'operation']
LogOutcome = Literal['success', 'failed']
Name = Annotated[str, Field(min_length=1, max_length=100)]
Code = Annotated[str, Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z][a-zA-Z0-9_.:-]*$')]
Remark = Annotated[str, Field(max_length=500)]
MAX_TEXT_BYTES = 131072


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', from_attributes=True)

    @field_validator('*')
    @classmethod
    def safe_text(cls, value: object) -> object:
        if isinstance(value, str):
            try:
                encoded = value.encode('utf-8')
            except UnicodeError:
                raise ValueError('文本必须为有效 Unicode') from None
            if '\x00' in value or len(encoded) > MAX_TEXT_BYTES:
                raise ValueError('文本包含非法字符或超过字节限制')
        return value

    @field_validator('name', 'title', 'code', 'key', 'label', 'value', check_fields=False)
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError('字段不能为空')
        return value.strip() if value is not None else value


class Patch(Contract):
    expected_version: Version

    @model_validator(mode='after')
    def nonnull(self) -> 'Patch':
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError('字段不能为 null')
        return self


class CreateConfig(Contract):
    key: Code
    name: Name
    value: Annotated[str, Field(min_length=1, max_length=1000)]
    remark: Remark = ''
    is_active: bool = True


class PatchConfig(Patch):
    key: Code | None = None
    name: Name | None = None
    value: Annotated[str, Field(min_length=1, max_length=1000)] | None = None
    remark: Remark | None = None
    is_active: bool | None = None


class CreateNotice(Contract):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    content: Annotated[str, Field(min_length=1, max_length=32000)]
    kind: NoticeKind = 'notice'
    status: NoticeStatus = 'draft'
    remark: Remark = ''

    @field_validator('content')
    @classmethod
    def nonblank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('公告正文不能为空')
        return value


class PatchNotice(Patch):
    title: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    content: Annotated[str, Field(min_length=1, max_length=32000)] | None = None
    kind: NoticeKind | None = None
    status: NoticeStatus | None = None
    remark: Remark | None = None

    @field_validator('content')
    @classmethod
    def nonblank_content(cls, value: str | None) -> str | None:
        return CreateNotice.nonblank_content(value) if value is not None else None


class EntityInfo(Contract):
    id: UUID
    version: int
    created_at: datetime
    updated_at: datetime


class ConfigInfo(EntityInfo, CreateConfig):
    pass


class NoticeInfo(EntityInfo, CreateNotice):
    # Consumers must render content using text interpolation, never as HTML.
    content_format: Literal['plain_text'] = 'plain_text'


class LogInfo(Contract):
    id: UUID
    kind: LogKind
    action: str
    username: str
    resource_type: str
    resource_id: str | None
    outcome: LogOutcome
    message: str
    created_at: datetime


Item = TypeVar('Item')


class Page(Contract, Generic[Item]):
    items: list[Item]
    total: int
    page: int
    page_size: int
