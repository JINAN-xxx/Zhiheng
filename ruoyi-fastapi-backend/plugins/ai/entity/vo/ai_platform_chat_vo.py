import unicodedata
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, field_validator
from pydantic.alias_generators import to_camel

from plugins.ai.domain.resource_id import validate_resource_id

SessionResourceId = Annotated[str, AfterValidator(lambda value: validate_resource_id(value, 'ses'))]
ModelResourceId = Annotated[str, AfterValidator(lambda value: validate_resource_id(value, 'mdl'))]
MessageResourceId = Annotated[str, AfterValidator(lambda value: validate_resource_id(value, 'msg'))]
RunResourceId = Annotated[str, AfterValidator(lambda value: validate_resource_id(value, 'run'))]
BindingResourceId = Annotated[str, AfterValidator(lambda value: validate_resource_id(value, 'bnd'))]
ClientMessageId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=100, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]*$'),
]
ChatContent = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=100_000)]
SystemPrompt = Annotated[str, StringConstraints(strict=True, max_length=10_000)]
NonNegativeInteger = Annotated[int, Field(strict=True, ge=0)]


def _parse_http_query_integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError('分页参数必须是整数')
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ValueError('分页参数必须是整数')


HttpQueryInteger = Annotated[int, BeforeValidator(_parse_http_query_integer)]


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        extra='forbid',
    )


class _ResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        from_attributes=True,
        extra='ignore',
    )


class PlatformImageReferenceModel(_StrictRequestModel):
    file_id: UUID


class PlatformChatRequestModel(_StrictRequestModel):
    session_id: SessionResourceId | None = None
    model_id: ModelResourceId
    client_message_id: ClientMessageId
    message: ChatContent = Field(repr=False)
    images: list[PlatformImageReferenceModel] = Field(default_factory=list, max_length=10)

    @field_validator('message')
    @classmethod
    def reject_blank_or_unsafe_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('消息内容不能为空')
        if any(
            unicodedata.category(character) in {'Cc', 'Zl', 'Zp'} and character not in {'\n', '\r', '\t'}
            for character in value
        ):
            raise ValueError('消息内容包含不支持的控制字符')
        return value


class PlatformSessionPageQueryModel(_StrictRequestModel):
    page_num: HttpQueryInteger = Field(default=1, ge=1)
    page_size: HttpQueryInteger = Field(default=20, ge=1, le=100)


class PlatformSessionRenameRequestModel(_StrictRequestModel):
    title: Annotated[
        str,
        StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200),
    ]

    @field_validator('title')
    @classmethod
    def reject_unsafe_title(cls, value: str) -> str:
        if any(unicodedata.category(character) in {'Cc', 'Zl', 'Zp'} for character in value):
            raise ValueError('会话标题包含不支持的控制字符')
        return value


class PlatformChatPreferenceModel(_StrictRequestModel):
    temperature: float | None = Field(default=None, strict=True, ge=0, le=2)
    add_history: bool = Field(default=True, strict=True)
    history_message_count: int = Field(default=20, strict=True, ge=0, le=100)
    system_prompt: SystemPrompt | None = Field(default=None, repr=False)
    metrics_visible: bool = Field(default=True, strict=True)
    vision_enabled: bool = Field(default=True, strict=True)
    image_max_size_mb: int = Field(default=10, strict=True, ge=1, le=50)


class PlatformSessionModel(_ResponseModel):
    session_id: SessionResourceId
    model_id: ModelResourceId
    title: str
    status: Literal['active', 'archived']
    created_at: datetime
    updated_at: datetime


class PlatformMessageModel(_ResponseModel):
    message_id: MessageResourceId
    session_id: SessionResourceId
    client_message_id: ClientMessageId
    role: Literal['user', 'assistant']
    content: str
    reasoning_content: str | None = None
    images: list[PlatformImageReferenceModel]
    status: Literal['complete', 'interrupted', 'cancelled']
    created_at: datetime
    updated_at: datetime


class PlatformRunModel(_ResponseModel):
    run_id: RunResourceId
    session_id: SessionResourceId
    model_id: ModelResourceId
    binding_id: BindingResourceId
    engine_type: str
    engine_model_snapshot: str
    engine_request_id: str | None = None
    trace_id: str | None = None
    status: Literal['pending', 'running', 'completed', 'cancelled', 'interrupted', 'failed']
    input_tokens: NonNegativeInteger | None = None
    output_tokens: NonNegativeInteger | None = None
    reasoning_tokens: NonNegativeInteger | None = None
    cache_read_tokens: NonNegativeInteger | None = None
    cache_write_tokens: NonNegativeInteger | None = None
    raw_cost: Decimal | None = None
    error_code: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PlatformSessionDetailModel(_ResponseModel):
    session: PlatformSessionModel
    messages: list[PlatformMessageModel] = Field(default_factory=list)
    runs: list[PlatformRunModel] = Field(default_factory=list)


class CancelRunRequestModel(_StrictRequestModel):
    run_id: RunResourceId
