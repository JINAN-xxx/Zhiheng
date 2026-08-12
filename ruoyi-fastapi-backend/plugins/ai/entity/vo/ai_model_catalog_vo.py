from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from pydantic.alias_generators import to_camel

ModelType = Literal['chat', 'embedding']
LifecycleStatus = Literal['draft', 'active', 'disabled']
BindingStatus = Literal['active', 'disabled']

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
ResourceIdentifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        extra='forbid',
    )


class _ResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
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


class ModelCatalogPageQueryModel(_StrictRequestModel):
    model_code: Annotated[str, StringConstraints(strict=True, max_length=100)] | None = None
    display_name: Annotated[str, StringConstraints(strict=True, max_length=100)] | None = None
    model_type: ModelType | None = None
    lifecycle_status: LifecycleStatus | None = None
    page_num: int = Field(default=1, strict=True, ge=1)
    page_size: int = Field(default=10, strict=True, ge=1, le=100)


class ResourceBindingUpsertModel(_StrictRequestModel):
    engine_resource_id: ResourceIdentifier
    binding_status: BindingStatus = 'active'
    engine_metadata: EngineMetadataModel = Field(default_factory=EngineMetadataModel)
    expected_version: PositiveInteger | None = None


class PlatformModelModel(_ResponseModel):
    model_id: str
    model_code: ModelCode
    display_name: DisplayName
    model_type: ModelType
    capabilities: ModelCapabilitiesModel
    context_window: PositiveInteger | None = None
    max_output_tokens: PositiveInteger | None = None
    default_parameters: ModelDefaultParametersModel
    lifecycle_status: LifecycleStatus
    published_at: datetime | None = None
    create_by: str
    create_time: datetime
    update_by: str
    update_time: datetime


class ResourceBindingModel(_ResponseModel):
    binding_id: str
    resource_id: str
    engine_resource_id: ResourceIdentifier
    binding_status: BindingStatus
    engine_metadata: EngineMetadataModel
    version: PositiveInteger
    create_time: datetime
    update_time: datetime


class PlatformModelDetailModel(PlatformModelModel):
    binding: ResourceBindingModel | None = None
