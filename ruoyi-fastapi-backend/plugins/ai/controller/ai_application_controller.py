from collections.abc import Awaitable
from datetime import datetime, timezone
from typing import Annotated, TypeVar

from fastapi import HTTPException, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from common.annotation.log_annotation import AuditedHTTPException, Log, RequestLogFieldRoot, ResponseLogFieldRoot
from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.enums import BusinessType
from common.router import APIRouterPro
from common.vo import DataResponseModel, PageResponseModel
from module_admin.entity.vo.user_vo import CurrentUserModel
from plugins.ai.domain.developer_api_errors import DeveloperApiError
from plugins.ai.entity.vo.ai_developer_api_vo import (
    ApplicationCreateModel,
    ApplicationModel,
    ApplicationPageQueryModel,
    ApplicationResourceId,
    ApplicationUpdateModel,
    DeveloperKeyCreatedModel,
    DeveloperKeyCreateModel,
    DeveloperKeyResourceId,
    DeveloperKeyRotateModel,
    DeveloperKeySummaryModel,
)
from plugins.ai.infrastructure.developer_api_provider import (
    get_application_service,
    get_developer_key_service,
)
from plugins.ai.service.developer_key_service import DeveloperApiActor
from utils.response_util import ResponseUtil

ResultT = TypeVar('ResultT')
APPLICATION_ID_REQUEST_FIELD = RequestLogFieldRoot.PATH_PARAMS.field('application_id')
KEY_ID_REQUEST_FIELD = RequestLogFieldRoot.PATH_PARAMS.field('key_id')
APPLICATION_RESPONSE_FIELDS = (
    ResponseLogFieldRoot.CODE.value,
    ResponseLogFieldRoot.MSG.value,
    ResponseLogFieldRoot.DATA.field('application_id'),
)
KEY_RESPONSE_FIELDS = (
    ResponseLogFieldRoot.CODE.value,
    ResponseLogFieldRoot.MSG.value,
    ResponseLogFieldRoot.DATA.field('key_id'),
    ResponseLogFieldRoot.DATA.field('fingerprint'),
)

ai_application_controller = APIRouterPro(
    prefix='/ai/applications',
    order_num=19,
    tags=['模型服务-开发者应用'],
    dependencies=[PreAuthDependency()],
)


def _current_time() -> datetime:
    return datetime.now(timezone.utc)


def _current_actor(current_user: CurrentUserModel) -> DeveloperApiActor:
    user = current_user.user
    if user is None or user.user_id is None or not user.user_name:
        raise HTTPException(status_code=401, detail='当前用户信息无效')
    return DeveloperApiActor(
        user_id=user.user_id,
        username=user.user_name,
        is_admin=bool(user.admin),
    )


async def _call_developer_api(operation: Awaitable[ResultT]) -> ResultT:
    try:
        return await operation
    except DeveloperApiError as error:
        status_code = error.status_code
        detail = error.public_dict()
    raise AuditedHTTPException(
        status_code=status_code,
        detail=detail,
        audit_code=detail['code'],
        audit_message=detail['message'],
    ) from None


@ai_application_controller.get(
    '',
    summary='获取开发者应用分页列表',
    response_model=PageResponseModel[ApplicationModel],
    dependencies=[UserInterfaceAuthDependency('ai:application:list')],
)
async def list_applications(
    request: Request,
    query: Annotated[ApplicationPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del request
    actor = _current_actor(current_user)
    result = await _call_developer_api(
        get_application_service(query_db).list_applications(
            query,
            actor_user_id=actor.user_id,
            is_admin=actor.is_admin,
            is_page=True,
        )
    )
    return ResponseUtil.success(model_content=result)


@ai_application_controller.get(
    '/{application_id}',
    summary='获取开发者应用详情',
    response_model=DataResponseModel[ApplicationModel],
    dependencies=[UserInterfaceAuthDependency('ai:application:query')],
)
async def get_application(
    request: Request,
    application_id: Annotated[ApplicationResourceId, Path(description='Application ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del request
    actor = _current_actor(current_user)
    result = await _call_developer_api(
        get_application_service(query_db).get_application(
            application_id,
            actor_user_id=actor.user_id,
            is_admin=actor.is_admin,
        )
    )
    return ResponseUtil.success(data=result)


@ai_application_controller.post(
    '',
    summary='创建开发者应用',
    response_model=DataResponseModel[ApplicationModel],
    dependencies=[UserInterfaceAuthDependency('ai:application:add')],
)
@Log(
    title='开发者应用管理',
    business_type=BusinessType.INSERT,
    request_log_mode='none',
    response_log_mode='include',
    response_include_fields=APPLICATION_RESPONSE_FIELDS,
)
async def create_application(
    request: Request,
    payload: ApplicationCreateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del request
    actor = _current_actor(current_user)
    result = await _call_developer_api(
        get_application_service(query_db).create_application(
            payload,
            owner_user_id=actor.user_id,
            username=actor.username,
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_application_controller.patch(
    '/{application_id}',
    summary='更新开发者应用',
    response_model=DataResponseModel[ApplicationModel],
    dependencies=[UserInterfaceAuthDependency('ai:application:edit')],
)
@Log(
    title='开发者应用管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='include',
    request_include_fields=(APPLICATION_ID_REQUEST_FIELD,),
    response_log_mode='include',
    response_include_fields=APPLICATION_RESPONSE_FIELDS,
)
async def update_application(
    request: Request,
    application_id: Annotated[ApplicationResourceId, Path(description='Application ID')],
    payload: ApplicationUpdateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del request
    actor = _current_actor(current_user)
    result = await _call_developer_api(
        get_application_service(query_db).update_application(
            application_id,
            payload,
            actor_user_id=actor.user_id,
            is_admin=actor.is_admin,
            username=actor.username,
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_application_controller.post(
    '/{application_id}/disable',
    summary='停用开发者应用',
    response_model=DataResponseModel[ApplicationModel],
    dependencies=[UserInterfaceAuthDependency('ai:application:disable')],
)
@Log(
    title='开发者应用管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='include',
    request_include_fields=(APPLICATION_ID_REQUEST_FIELD,),
    response_log_mode='include',
    response_include_fields=APPLICATION_RESPONSE_FIELDS,
)
async def disable_application(
    request: Request,
    application_id: Annotated[ApplicationResourceId, Path(description='Application ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del request
    actor = _current_actor(current_user)
    result = await _call_developer_api(
        get_application_service(query_db).disable_application(
            application_id,
            actor_user_id=actor.user_id,
            is_admin=actor.is_admin,
            username=actor.username,
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_application_controller.get(
    '/{application_id}/keys',
    summary='获取开发者 API Key 列表',
    response_model=DataResponseModel[list[DeveloperKeySummaryModel]],
    dependencies=[UserInterfaceAuthDependency('ai:application:key:list')],
)
async def list_developer_keys(
    request: Request,
    application_id: Annotated[ApplicationResourceId, Path(description='Application ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del request
    result = await _call_developer_api(
        get_developer_key_service(query_db).list_keys(application_id, actor=_current_actor(current_user))
    )
    return ResponseUtil.success(data=result)


@ai_application_controller.post(
    '/{application_id}/keys',
    summary='创建开发者 API Key',
    response_model=DataResponseModel[DeveloperKeyCreatedModel],
    dependencies=[UserInterfaceAuthDependency('ai:application:key:add')],
)
@Log(
    title='开发者 API Key 管理',
    business_type=BusinessType.INSERT,
    request_log_mode='include',
    request_include_fields=(APPLICATION_ID_REQUEST_FIELD,),
    response_log_mode='include',
    response_include_fields=KEY_RESPONSE_FIELDS,
)
async def create_developer_key(
    request: Request,
    application_id: Annotated[ApplicationResourceId, Path(description='Application ID')],
    payload: DeveloperKeyCreateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del request
    result = await _call_developer_api(
        get_developer_key_service(query_db).create_key(
            application_id,
            payload,
            actor=_current_actor(current_user),
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_application_controller.post(
    '/{application_id}/keys/{key_id}/rotate',
    summary='轮换开发者 API Key',
    response_model=DataResponseModel[DeveloperKeyCreatedModel],
    dependencies=[UserInterfaceAuthDependency('ai:application:key:rotate')],
)
@Log(
    title='开发者 API Key 管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='include',
    request_include_fields=(APPLICATION_ID_REQUEST_FIELD, KEY_ID_REQUEST_FIELD),
    response_log_mode='include',
    response_include_fields=KEY_RESPONSE_FIELDS,
)
async def rotate_developer_key(
    request: Request,
    application_id: Annotated[ApplicationResourceId, Path(description='Application ID')],
    key_id: Annotated[DeveloperKeyResourceId, Path(description='API Key ID')],
    payload: DeveloperKeyRotateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del request
    result = await _call_developer_api(
        get_developer_key_service(query_db).rotate_key(
            application_id,
            key_id,
            payload,
            actor=_current_actor(current_user),
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)


@ai_application_controller.post(
    '/{application_id}/keys/{key_id}/revoke',
    summary='撤销开发者 API Key',
    response_model=DataResponseModel[DeveloperKeySummaryModel],
    dependencies=[UserInterfaceAuthDependency('ai:application:key:revoke')],
)
@Log(
    title='开发者 API Key 管理',
    business_type=BusinessType.UPDATE,
    request_log_mode='include',
    request_include_fields=(APPLICATION_ID_REQUEST_FIELD, KEY_ID_REQUEST_FIELD),
    response_log_mode='include',
    response_include_fields=KEY_RESPONSE_FIELDS,
)
async def revoke_developer_key(
    request: Request,
    application_id: Annotated[ApplicationResourceId, Path(description='Application ID')],
    key_id: Annotated[DeveloperKeyResourceId, Path(description='API Key ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    del request
    result = await _call_developer_api(
        get_developer_key_service(query_db).revoke_key(
            application_id,
            key_id,
            actor=_current_actor(current_user),
            now=_current_time(),
        )
    )
    return ResponseUtil.success(data=result)
