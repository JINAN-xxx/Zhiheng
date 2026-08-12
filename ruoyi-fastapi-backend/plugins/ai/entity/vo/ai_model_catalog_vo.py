import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from plugins.ai.domain.resource_id import validate_resource_id

ModelType = Literal['chat', 'embedding']
LifecycleStatus = Literal['draft', 'active', 'disabled']
BindingStatus = Literal['active', 'disabled']
FORBIDDEN_RESOURCE_ID_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})

ModelCode = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=100,
        pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$',
    ),
]
DisplayName = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]
PositiveInteger = Annotated[int, Field(strict=True, gt=0)]
ModelResourceId = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(lambda value: validate_resource_id(value, 'mdl')),
]
BindingResourceId = Annotated[
    str,
    StringConstraints(strict=True),
    AfterValidator(lambda value: validate_resource_id(value, 'bnd')),
]


def _parse_http_query_integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError('分页参数必须是整数')
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    raise ValueError('分页参数必须是整数')


HttpQueryInteger = Annotated[int, BeforeValidator(_parse_http_query_integer)]


def _reject_control_characters(value: str) -> str:
    if any(unicodedata.category(character) in FORBIDDEN_RESOURCE_ID_CATEGORIES for character in value):
        raise ValueError('引擎资源标识不能包含控制字符')
    return value


ResourceIdentifier = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255),
    AfterValidator(_reject_control_characters),
]


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


class ModelCapabilitiesModel(_StrictRequestModel):
    vision: bool = Field(default=False, strict=True)
    reasoning: bool = Field(default=False, strict=True)
    tools: bool = Field(default=False, strict=True)
    streaming: bool = Field(default=False, strict=True)


class ModelDefaultParametersModel(_StrictRequestModel):
    temperature: float | None = Field(default=None, strict=True, ge=0, le=2)
    top_p: float | None = Field(default=None, strict=True, ge=0, le=1)
    max_tokens: PositiveInteger | None = None
    presence_penalty: float | None = Field(default=None, strict=True, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, strict=True, ge=-2, le=2)


class EngineMetadataModel(_StrictRequestModel):
    """本阶段不允许 Platform API 写入未定义的引擎元数据。"""


class _ModelCapabilitiesResponseModel(_ResponseModel):
    vision: bool = Field(default=False, strict=True)
    reasoning: bool = Field(default=False, strict=True)
    tools: bool = Field(default=False, strict=True)
    streaming: bool = Field(default=False, strict=True)


class _ModelDefaultParametersResponseModel(_ResponseModel):
    temperature: float | None = Field(default=None, strict=True, ge=0, le=2)
    top_p: float | None = Field(default=None, strict=True, ge=0, le=1)
    max_tokens: PositiveInteger | None = None
    presence_penalty: float | None = Field(default=None, strict=True, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, strict=True, ge=-2, le=2)


class _EngineMetadataResponseModel(_ResponseModel):
    """响应侧非敏感引擎元数据。"""


class ModelCatalogCreateModel(_StrictRequestModel):
    model_code: ModelCode
    display_name: DisplayName
    model_type: ModelType
    capabilities: ModelCapabilitiesModel = Field(default_factory=ModelCapabilitiesModel)
    context_window: PositiveInteger | None = None
    max_output_tokens: PositiveInteger | None = None
    default_parameters: ModelDefaultParametersModel = Field(default_factory=ModelDefaultParametersModel)


class ModelCatalogUpdateModel(_StrictRequestModel):
    model_code: ModelCode | None = None
    display_name: DisplayName | None = None
    model_type: ModelType | None = None
    capabilities: ModelCapabilitiesModel | None = None
    context_window: PositiveInteger | None = None
    max_output_tokens: PositiveInteger | None = None
    default_parameters: ModelDefaultParametersModel | None = None

    @model_validator(mode='before')
    @classmethod
    def reject_explicit_null_for_non_nullable_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        non_nullable_aliases = ('modelCode', 'displayName', 'modelType', 'capabilities', 'defaultParameters')
        null_fields = [field for field in non_nullable_aliases if field in value and value[field] is None]
        if null_fields:
            raise ValueError(f'{", ".join(null_fields)} 不允许为 null')
        return value


class ModelCatalogPageQueryModel(_StrictRequestModel):
    model_code: Annotated[str, StringConstraints(strict=True, max_length=100)] | None = None
    display_name: Annotated[str, StringConstraints(strict=True, max_length=100)] | None = None
    model_type: ModelType | None = None
    lifecycle_status: LifecycleStatus | None = None
    page_num: HttpQueryInteger = Field(default=1, ge=1)
    page_size: HttpQueryInteger = Field(default=10, ge=1, le=100)


class ResourceBindingUpsertModel(_StrictRequestModel):
    engine_resource_id: ResourceIdentifier
    binding_status: BindingStatus = 'active'
    engine_metadata: EngineMetadataModel = Field(default_factory=EngineMetadataModel)
    expected_version: PositiveInteger | None = None


class PlatformModelModel(_ResponseModel):
    model_id: ModelResourceId
    model_code: ModelCode
    display_name: DisplayName
    model_type: ModelType
    capabilities: _ModelCapabilitiesResponseModel
    context_window: PositiveInteger | None = None
    max_output_tokens: PositiveInteger | None = None
    default_parameters: _ModelDefaultParametersResponseModel
    lifecycle_status: LifecycleStatus
    published_at: datetime | None = None
    create_by: str
    create_time: datetime
    update_by: str
    update_time: datetime


class ResourceBindingModel(_ResponseModel):
    binding_id: BindingResourceId
    resource_id: ModelResourceId
    engine_resource_id: ResourceIdentifier
    binding_status: BindingStatus
    engine_metadata: _EngineMetadataResponseModel
    version: PositiveInteger
    create_time: datetime
    update_time: datetime


class PlatformModelDetailModel(PlatformModelModel):
    binding: ResourceBindingModel | None = None
