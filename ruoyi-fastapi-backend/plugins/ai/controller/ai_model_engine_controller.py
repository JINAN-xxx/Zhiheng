from collections.abc import Awaitable
from typing import Annotated, TypeVar

from fastapi import HTTPException, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from common.annotation.log_annotation import AuditedHTTPException, Log, RequestLogFieldRoot, ResponseLogFieldRoot
from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.enums import BusinessType
from common.router import APIRouterPro
from common.vo import DataResponseModel, ResponseBaseModel
from module_admin.entity.vo.user_vo import CurrentUserModel
from plugins.ai.entity.vo.ai_model_engine_vo import (
    ConnectionTestRequestModel,
    ConnectionTestResultModel,
    CredentialCreateModel,
    CredentialName,
    CredentialRotateModel,
    CredentialSummaryModel,
    DeploymentCreateModel,
    DeploymentModel,
    DeploymentUpdateModel,
    ModelEngineStatusModel,
    ProviderModel,
    SafeIdentifier,
)
from plugins.ai.infrastructure.litellm_management_errors import LiteLLMManagementError
from plugins.ai.infrastructure.model_engine_provider import get_model_engine_service
from plugins.ai.service.model_engine_service import ModelEngineServiceError
from utils.response_util import ResponseUtil

ResultT = TypeVar('ResultT')
CREDENTIAL_NAME_FIELD = RequestLogFieldRoot.PATH_PARAMS.field('credential_name')
DEPLOYMENT_ID_FIELD = RequestLogFieldRoot.PATH_PARAMS.field('deployment_id')
COMMAND_RESPONSE_FIELDS = (ResponseLogFieldRoot.CODE.value, ResponseLogFieldRoot.MSG.value)
DEPLOYMENT_RESPONSE_FIELDS = (*COMMAND_RESPONSE_FIELDS, ResponseLogFieldRoot.DATA.field('deployment_id'))
CONNECTION_RESPONSE_FIELDS = (
    *COMMAND_RESPONSE_FIELDS,
    ResponseLogFieldRoot.DATA.field('success'),
    ResponseLogFieldRoot.DATA.field('failure_reason'),
)

ai_model_engine_controller = APIRouterPro(
    prefix='/ai/model-engine',
    order_num=20,
    tags=['模型服务-引擎控制面'],
    dependencies=[PreAuthDependency()],
)


def _current_username(current_user: CurrentUserModel) -> str:
    if current_user.user is None or not current_user.user.user_name:
        raise HTTPException(status_code=401, detail='当前用户信息无效')
    return current_user.user.user_name


async def _call_engine(operation: Awaitable[ResultT]) -> ResultT:
    try:
        return await operation
    except LiteLLMManagementError as error:
        detail: dict[str, object] = {'code': error.code, 'message': error.default_message}
        status_code = error.status_code
    except ModelEngineServiceError as error:
        detail = error.public_dict()
        status_code = error.status_code
    raise AuditedHTTPException(
        status_code=status_code,
        detail=detail,
        audit_code=str(detail['code']),
        audit_message=str(detail['message']),
    ) from None


@ai_model_engine_controller.get(
    '/providers',
    summary='获取引擎 Provider 字段',
    response_model=DataResponseModel[list[ProviderModel]],
    dependencies=[UserInterfaceAuthDependency('ai:engine:credential:list')],
)
async def list_engine_providers(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    del query_db
    result = await _call_engine(get_model_engine_service(request).list_providers())
    return ResponseUtil.success(data=result)


@ai_model_engine_controller.get(
    '/credentials',
    summary='获取引擎凭据列表',
    response_model=DataResponseModel[list[CredentialSummaryModel]],
    dependencies=[UserInterfaceAuthDependency('ai:engine:credential:list')],
)
async def list_engine_credentials(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    del query_db
    result = await _call_engine(get_model_engine_service(request).list_credentials())
    return ResponseUtil.success(data=result)


@ai_model_engine_controller.post(
    '/credentials',
    summary='创建引擎凭据',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('ai:engine:credential:add')],
)
@Log(
    title='模型引擎凭据管理',
    business_type=BusinessType.INSERT,
    request_log_mode='exclude',
    request_exclude_fields=(RequestLogFieldRoot.JSON_BODY.value,),
    response_log_mode='include',
    response_include_fields=COMMAND_RESPONSE_FIELDS,
)
async def create_engine_credential(
    request: Request,
    payload: CredentialCreateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del query_db
    _current_username(current_user)
    await _call_engine(get_model_engine_service(request).create_credential(payload))
    return ResponseUtil.success()


@ai_model_engine_controller.put(
    '/credentials/{credential_name}/values',
    summary='轮换引擎凭据值',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('ai:engine:credential:rotate')],
)
@Log(
    title='模型引擎凭据管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='exclude',
    request_exclude_fields=(RequestLogFieldRoot.JSON_BODY.value,),
    response_log_mode='include',
    response_include_fields=COMMAND_RESPONSE_FIELDS,
)
async def rotate_engine_credential(
    request: Request,
    credential_name: Annotated[CredentialName, Path(description='引擎凭据名称')],
    payload: CredentialRotateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del query_db
    _current_username(current_user)
    await _call_engine(get_model_engine_service(request).rotate_credential(credential_name, payload))
    return ResponseUtil.success()


@ai_model_engine_controller.delete(
    '/credentials/{credential_name}',
    summary='删除引擎凭据',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('ai:engine:credential:remove')],
)
@Log(
    title='模型引擎凭据管理',
    business_type=BusinessType.DELETE,
    request_log_mode='include',
    request_include_fields=(CREDENTIAL_NAME_FIELD,),
    response_log_mode='include',
    response_include_fields=COMMAND_RESPONSE_FIELDS,
)
async def delete_engine_credential(
    request: Request,
    credential_name: Annotated[CredentialName, Path(description='引擎凭据名称')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del query_db
    _current_username(current_user)
    await _call_engine(get_model_engine_service(request).delete_credential(credential_name))
    return ResponseUtil.success()


@ai_model_engine_controller.get(
    '/deployments',
    summary='获取引擎部署列表',
    response_model=DataResponseModel[list[DeploymentModel]],
    dependencies=[UserInterfaceAuthDependency('ai:engine:deployment:list')],
)
async def list_engine_deployments(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    model_alias: Annotated[SafeIdentifier | None, Query(alias='modelAlias')] = None,
) -> Response:
    del query_db
    service = get_model_engine_service(request)
    operation = service.list_deployments(model_alias=model_alias) if model_alias is not None else service.list_deployments()
    result = await _call_engine(operation)
    return ResponseUtil.success(data=result)


@ai_model_engine_controller.get(
    '/deployments/{deployment_id}',
    summary='获取引擎部署详情',
    response_model=DataResponseModel[DeploymentModel],
    dependencies=[UserInterfaceAuthDependency('ai:engine:deployment:query')],
)
async def get_engine_deployment(
    request: Request,
    deployment_id: Annotated[SafeIdentifier, Path(description='引擎部署ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    del query_db
    result = await _call_engine(get_model_engine_service(request).get_deployment(deployment_id))
    return ResponseUtil.success(data=result)


@ai_model_engine_controller.post(
    '/deployments',
    summary='创建引擎部署',
    response_model=DataResponseModel[DeploymentModel],
    dependencies=[UserInterfaceAuthDependency('ai:engine:deployment:add')],
)
@Log(
    title='模型引擎部署管理',
    business_type=BusinessType.INSERT,
    request_log_mode='none',
    response_log_mode='include',
    response_include_fields=DEPLOYMENT_RESPONSE_FIELDS,
)
async def create_engine_deployment(
    request: Request,
    payload: DeploymentCreateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del query_db
    _current_username(current_user)
    result = await _call_engine(get_model_engine_service(request).create_deployment(payload))
    return ResponseUtil.success(data=result)


@ai_model_engine_controller.patch(
    '/deployments/{deployment_id}',
    summary='更新引擎部署',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('ai:engine:deployment:edit')],
)
@Log(
    title='模型引擎部署管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='include',
    request_include_fields=(DEPLOYMENT_ID_FIELD,),
    response_log_mode='include',
    response_include_fields=COMMAND_RESPONSE_FIELDS,
)
async def update_engine_deployment(
    request: Request,
    deployment_id: Annotated[SafeIdentifier, Path(description='引擎部署ID')],
    payload: DeploymentUpdateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del query_db
    _current_username(current_user)
    await _call_engine(get_model_engine_service(request).update_deployment(deployment_id, payload))
    return ResponseUtil.success()


@ai_model_engine_controller.delete(
    '/deployments/{deployment_id}',
    summary='删除引擎部署',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('ai:engine:deployment:remove')],
)
@Log(
    title='模型引擎部署管理',
    business_type=BusinessType.DELETE,
    request_log_mode='include',
    request_include_fields=(DEPLOYMENT_ID_FIELD,),
    response_log_mode='include',
    response_include_fields=COMMAND_RESPONSE_FIELDS,
)
async def delete_engine_deployment(
    request: Request,
    deployment_id: Annotated[SafeIdentifier, Path(description='引擎部署ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del query_db
    _current_username(current_user)
    await _call_engine(get_model_engine_service(request).delete_deployment(deployment_id))
    return ResponseUtil.success()


@ai_model_engine_controller.post(
    '/test-connection',
    summary='测试引擎模型连接',
    response_model=DataResponseModel[ConnectionTestResultModel],
    dependencies=[UserInterfaceAuthDependency('ai:model:test')],
)
@Log(
    title='模型引擎连接测试',
    business_type=BusinessType.OTHER,
    request_log_mode='exclude',
    request_exclude_fields=(RequestLogFieldRoot.JSON_BODY.value,),
    response_log_mode='include',
    response_include_fields=CONNECTION_RESPONSE_FIELDS,
)
async def test_engine_connection(
    request: Request,
    payload: ConnectionTestRequestModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del query_db
    _current_username(current_user)
    result = await _call_engine(get_model_engine_service(request).test_connection(payload))
    return ResponseUtil.success(data=result)


@ai_model_engine_controller.get(
    '/status',
    summary='获取模型引擎运行状态',
    response_model=DataResponseModel[ModelEngineStatusModel],
    dependencies=[UserInterfaceAuthDependency('ai:runtime:query')],
)
async def get_model_engine_status(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    del query_db
    result = await _call_engine(get_model_engine_service(request).get_status())
    return ResponseUtil.success(data=result)
