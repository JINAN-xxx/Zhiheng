import unicodedata
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator
from pydantic.alias_generators import to_camel

from plugins.ai.domain.resource_id import validate_resource_id

ModelType = Literal['chat', 'embedding', 'rerank']
LifecycleStatus = Literal['draft', 'active', 'disabled']
BindingStatus = Literal['active', 'disabled']
FORBIDDEN_RESOURCE_ID_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})

# 类型化 Capability 白名单：未知 Capability 拒绝写入，不透传 LiteLLM 任意元数据。
_CAPABILITIES_BY_TYPE: dict[str, frozenset[str]] = {
    'chat': frozenset({'streaming', 'vision', 'reasoning', 'tools'}),
    'embedding': frozenset({'dimensions_override'}),
    'rerank': frozenset({'return_documents'}),
}
# 类型化默认参数白名单：按 model_type 使用互斥白名单验证。
_DEFAULT_PARAMETERS_BY_TYPE: dict[str, frozenset[str]] = {
    'chat': frozenset({'temperature', 'top_p', 'max_tokens', 'presence_penalty', 'frequency_penalty'}),
    'embedding': frozenset({'dimensions'}),
    'rerank': frozenset({'top_n', 'return_documents'}),
}
_INVALID_TYPE_MESSAGE = '模型类型与能力或默认参数不匹配'

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


def _reject_control_characters(value: object) -> object:
    if isinstance(value, str) and any(
        unicodedata.category(character) in FORBIDDEN_RESOURCE_ID_CATEGORIES for character in value
    ):
        raise ValueError('引擎资源标识不能包含控制字符')
    return value


ResourceIdentifier = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=255),
    BeforeValidator(_reject_control_characters),
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
    dimensions_override: bool = Field(default=False, strict=True)
    return_documents: bool = Field(default=False, strict=True)


class ModelDefaultParametersModel(_StrictRequestModel):
    temperature: float | None = Field(default=None, strict=True, ge=0, le=2)
    top_p: float | None = Field(default=None, strict=True, ge=0, le=1)
    max_tokens: PositiveInteger | None = None
    presence_penalty: float | None = Field(default=None, strict=True, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, strict=True, ge=-2, le=2)
    dimensions: PositiveInteger | None = None
    top_n: PositiveInteger | None = None
    return_documents: bool | None = Field(default=None, strict=True)


class EngineMetadataModel(_StrictRequestModel):
    """本阶段不允许 Platform API 写入未定义的引擎元数据。"""


class _ModelCapabilitiesResponseModel(_ResponseModel):
    vision: bool = Field(default=False, strict=True)
    reasoning: bool = Field(default=False, strict=True)
    tools: bool = Field(default=False, strict=True)
    streaming: bool = Field(default=False, strict=True)
    dimensions_override: bool = Field(default=False, strict=True)
    return_documents: bool = Field(default=False, strict=True)


class _ModelDefaultParametersResponseModel(_ResponseModel):
    temperature: float | None = Field(default=None, strict=True, ge=0, le=2)
    top_p: float | None = Field(default=None, strict=True, ge=0, le=1)
    max_tokens: PositiveInteger | None = None
    presence_penalty: float | None = Field(default=None, strict=True, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, strict=True, ge=-2, le=2)
    dimensions: PositiveInteger | None = None
    top_n: PositiveInteger | None = None
    return_documents: bool | None = Field(default=None, strict=True)


class _EngineMetadataResponseModel(_ResponseModel):
    """响应侧非敏感引擎元数据。"""


def _validate_typed_fields(
    model_type: str,
    capabilities: ModelCapabilitiesModel,
    default_parameters: ModelDefaultParametersModel,
) -> None:
    """按 model_type 校验 Capability 与默认参数的白名单互斥约束。"""
    capability_keys = set(capabilities.model_fields_set)
    if capability_keys.difference(_CAPABILITIES_BY_TYPE[model_type]):
        raise ValueError(_INVALID_TYPE_MESSAGE)
    # 显式 null 等价于未设置，不参与类型白名单校验。
    parameter_keys = {
        key for key in default_parameters.model_fields_set if getattr(default_parameters, key) is not None
    }
    if parameter_keys.difference(_DEFAULT_PARAMETERS_BY_TYPE[model_type]):
        raise ValueError(_INVALID_TYPE_MESSAGE)


class ModelCatalogCreateModel(_StrictRequestModel):
    model_code: ModelCode
    display_name: DisplayName
    model_type: ModelType
    capabilities: ModelCapabilitiesModel = Field(default_factory=ModelCapabilitiesModel)
    context_window: PositiveInteger | None = None
    max_output_tokens: PositiveInteger | None = None
    default_parameters: ModelDefaultParametersModel = Field(default_factory=ModelDefaultParametersModel)

    @model_validator(mode='after')
    def validate_typed_fields(self) -> 'ModelCatalogCreateModel':
        _validate_typed_fields(self.model_type, self.capabilities, self.default_parameters)
        return self


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

    @model_validator(mode='after')
    def validate_typed_fields(self) -> 'ModelCatalogUpdateModel':
        model_type = self.model_type
        capabilities = self.capabilities
        default_parameters = self.default_parameters
        if model_type is None and capabilities is None and default_parameters is None:
            return self
        # 类型未变化时使用数据库既有类型校验；服务层会再校验类型不可变性。
        if model_type is None:
            return self
        _validate_typed_fields(
            model_type,
            capabilities if capabilities is not None else ModelCapabilitiesModel(),
            default_parameters if default_parameters is not None else ModelDefaultParametersModel(),
        )
        return self


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
