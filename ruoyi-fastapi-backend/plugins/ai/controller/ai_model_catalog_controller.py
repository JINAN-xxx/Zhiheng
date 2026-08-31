from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, TypeVar

from fastapi import HTTPException, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from common.annotation.log_annotation import AuditedHTTPException, Log, RequestLogFieldRoot, ResponseLogFieldRoot
from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.enums import BusinessType
from common.router import APIRouterPro
from common.vo import DataResponseModel, PageResponseModel, ResponseBaseModel
from config.database import AsyncSessionLocal
from module_admin.entity.vo.user_vo import CurrentUserModel
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.vo.ai_developer_api_vo import RateCardCreateModel, RateCardModel
from plugins.ai.entity.vo.ai_model_catalog_vo import (
    ModelCatalogCreateModel,
    ModelCatalogPageQueryModel,
    ModelCatalogUpdateModel,
    ModelResourceId,
    PlatformModelDetailModel,
    PlatformModelModel,
    ResourceBindingModel,
    ResourceBindingUpsertModel,
)
from plugins.ai.infrastructure.model_runtime_provider import get_model_runtime_engine
from plugins.ai.service.model_catalog_service import ModelCatalogService
from plugins.ai.service.model_service import ModelService
from plugins.ai.service.model_smoke_test_service import ModelSmokeTestService
from utils.response_util import ResponseUtil

ResultT = TypeVar('ResultT')
MODEL_ID_REQUEST_FIELD = RequestLogFieldRoot.PATH_PARAMS.field('model_id')
MODEL_RESPONSE_FIELDS = (
    ResponseLogFieldRoot.CODE.value,
    ResponseLogFieldRoot.MSG.value,
    ResponseLogFieldRoot.DATA.field('model_id'),
)
BINDING_RESPONSE_FIELDS = (
    ResponseLogFieldRoot.CODE.value,
    ResponseLogFieldRoot.MSG.value,
    ResponseLogFieldRoot.DATA.field('binding_id'),
)

ai_model_catalog_controller = APIRouterPro(
    prefix='/ai/models',
    order_num=18,
    tags=['模型服务-模型中心'],
    dependencies=[PreAuthDependency()],
)


def _current_time() -> datetime:
    return datetime.now()


def _current_username(current_user: CurrentUserModel) -> str:
    if current_user.user is None or not current_user.user.user_name:
        raise HTTPException(status_code=401, detail='当前用户信息无效')
    return current_user.user.user_name


async def _call_catalog(operation: Awaitable[ResultT]) -> ResultT:
    try:
        return await operation
    except ModelServiceError as error:
        status_code = error.status_code
        detail = error.public_dict()
    raise AuditedHTTPException(
        status_code=status_code,
        detail=detail,
        audit_code=detail['code'],
        audit_message=detail['message'],
    ) from None


def _catalog_service(request: Request, query_db: AsyncSession) -> ModelCatalogService:
    return ModelCatalogService(query_db, get_model_runtime_engine(request))


@ai_model_catalog_controller.get(
    '',
    summary='获取平台模型分页列表',
    response_model=PageResponseModel[PlatformModelModel],
    dependencies=[UserInterfaceAuthDependency('ai:model:list')],
)
async def list_platform_models(
    request: Request,
    query: Annotated[ModelCatalogPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await _call_catalog(_catalog_service(request, query_db).list_models(query, is_page=True))
    return ResponseUtil.success(model_content=result)


@ai_model_catalog_controller.get(
    '/{model_id}',
    summary='获取平台模型详情',
    response_model=DataResponseModel[PlatformModelDetailModel],
    dependencies=[UserInterfaceAuthDependency('ai:model:query')],
)
async def get_platform_model(
    request: Request,
    model_id: Annotated[ModelResourceId, Path(description='平台模型ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await _call_catalog(_catalog_service(request, query_db).get_model(model_id))
    return ResponseUtil.success(data=result)


@ai_model_catalog_controller.post(
    '',
    summary='创建平台模型草稿',
    response_model=DataResponseModel[PlatformModelModel],
    dependencies=[UserInterfaceAuthDependency('ai:model:add')],
)
@Log(
    title='平台模型管理',
    business_type=BusinessType.INSERT,
    request_log_mode='none',
    response_log_mode='include',
    response_include_fields=MODEL_RESPONSE_FIELDS,
)
async def create_platform_model(
    request: Request,
    payload: ModelCatalogCreateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call_catalog(
        _catalog_service(request, query_db).create_model(
            payload,
            username=_current_username(current_user),
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_model_catalog_controller.patch(
    '/{model_id}',
    summary='更新平台模型',
    response_model=DataResponseModel[PlatformModelModel],
    dependencies=[UserInterfaceAuthDependency('ai:model:edit')],
)
@Log(
    title='平台模型管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='include',
    request_include_fields=(MODEL_ID_REQUEST_FIELD,),
    response_log_mode='include',
    response_include_fields=MODEL_RESPONSE_FIELDS,
)
async def update_platform_model(
    request: Request,
    model_id: Annotated[ModelResourceId, Path(description='平台模型ID')],
    payload: ModelCatalogUpdateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call_catalog(
        _catalog_service(request, query_db).update_model(
            model_id,
            payload,
            username=_current_username(current_user),
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_model_catalog_controller.put(
    '/{model_id}/binding',
    summary='创建或更新平台模型运行时绑定',
    response_model=DataResponseModel[ResourceBindingModel],
    dependencies=[UserInterfaceAuthDependency('ai:model:edit')],
)
@Log(
    title='平台模型绑定管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='include',
    request_include_fields=(MODEL_ID_REQUEST_FIELD,),
    response_log_mode='include',
    response_include_fields=BINDING_RESPONSE_FIELDS,
)
async def upsert_platform_model_binding(
    request: Request,
    model_id: Annotated[ModelResourceId, Path(description='平台模型ID')],
    payload: ResourceBindingUpsertModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call_catalog(
        _catalog_service(request, query_db).upsert_binding(
            model_id,
            payload,
            username=_current_username(current_user),
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_model_catalog_controller.post(
    '/{model_id}/publish',
    summary='发布平台模型',
    response_model=DataResponseModel[PlatformModelModel],
    dependencies=[UserInterfaceAuthDependency('ai:model:publish')],
)
@Log(
    title='平台模型管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='include',
    request_include_fields=(MODEL_ID_REQUEST_FIELD,),
    response_log_mode='include',
    response_include_fields=MODEL_RESPONSE_FIELDS,
)
async def publish_platform_model(
    request: Request,
    model_id: Annotated[ModelResourceId, Path(description='平台模型ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call_catalog(
        _catalog_service(request, query_db).publish_model(
            model_id,
            username=_current_username(current_user),
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_model_catalog_controller.post(
    '/{model_id}/disable',
    summary='停用平台模型',
    response_model=DataResponseModel[PlatformModelModel],
    dependencies=[UserInterfaceAuthDependency('ai:model:edit')],
)
@Log(
    title='平台模型管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='include',
    request_include_fields=(MODEL_ID_REQUEST_FIELD,),
    response_log_mode='include',
    response_include_fields=MODEL_RESPONSE_FIELDS,
)
async def disable_platform_model(
    request: Request,
    model_id: Annotated[ModelResourceId, Path(description='平台模型ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call_catalog(
        _catalog_service(request, query_db).disable_model(
            model_id,
            username=_current_username(current_user),
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_model_catalog_controller.delete(
    '/{model_id}',
    summary='删除未发布的平台模型草稿',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('ai:model:remove')],
)
@Log(
    title='平台模型管理',
    business_type=BusinessType.DELETE,
    request_log_mode='include',
    request_include_fields=(MODEL_ID_REQUEST_FIELD,),
    response_log_mode='include',
    response_include_fields=(ResponseLogFieldRoot.CODE.value, ResponseLogFieldRoot.MSG.value),
)
async def delete_platform_model(
    request: Request,
    model_id: Annotated[ModelResourceId, Path(description='平台模型ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del current_user
    await _call_catalog(_catalog_service(request, query_db).delete_draft(model_id))
    return ResponseUtil.success()


@ai_model_catalog_controller.get(
    '/{model_id}/rate-cards',
    summary='获取平台模型价格卡列表',
    response_model=DataResponseModel[list[RateCardModel]],
    dependencies=[UserInterfaceAuthDependency('ai:model:query')],
)
async def list_model_rate_cards(
    request: Request,
    model_id: Annotated[ModelResourceId, Path(description='平台模型ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await _call_catalog(_catalog_service(request, query_db).list_rate_cards(model_id))
    return ResponseUtil.success(data=result)


@ai_model_catalog_controller.post(
    '/{model_id}/rate-cards',
    summary='创建平台模型价格卡新版本',
    response_model=DataResponseModel[RateCardModel],
    dependencies=[UserInterfaceAuthDependency('ai:model:edit')],
)
async def create_model_rate_card(
    request: Request,
    model_id: Annotated[ModelResourceId, Path(description='平台模型ID')],
    payload: RateCardCreateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call_catalog(
        _catalog_service(request, query_db).create_rate_card(
            model_id,
            payload,
            username=_current_username(current_user),
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_model_catalog_controller.post(
    '/{model_id}/smoke-test',
    summary='执行平台模型类型化连接测试',
    response_model=DataResponseModel[dict[str, object]],
    dependencies=[UserInterfaceAuthDependency('ai:model:test')],
)
async def smoke_test_platform_model(
    request: Request,
    model_id: Annotated[ModelResourceId, Path(description='平台模型ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    runtime = get_model_runtime_engine(request)
    service = ModelSmokeTestService(ModelService(query_db, runtime, session_factory=AsyncSessionLocal), runtime)
    result = await service.run(model_id)
    return ResponseUtil.success(data=result.model_dump(by_alias=True, mode='json'))
