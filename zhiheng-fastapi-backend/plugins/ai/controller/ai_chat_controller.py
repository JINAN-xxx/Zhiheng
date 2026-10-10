import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import Body, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from common.annotation.log_annotation import AuditedHTTPException, Log
from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.enums import BusinessType
from common.router import APIRouterPro
from common.vo import DataResponseModel, PageResponseModel, ResponseBaseModel
from module_admin.entity.vo.user_vo import CurrentUserModel
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.vo.ai_platform_chat_vo import (
    CancelRunRequestModel,
    PlatformChatPreferenceModel,
    PlatformChatRequestModel,
    PlatformSessionDetailModel,
    PlatformSessionModel,
    PlatformSessionPageQueryModel,
    PlatformSessionRenameRequestModel,
    PlatformSessionStatusRequestModel,
)
from plugins.ai.infrastructure.platform_chat_provider import get_platform_chat_service
from plugins.ai.public.api_transport import ManagedStreamingResponse
from plugins.ai.service.chat_preferences_service import ChatPreferencesService
from plugins.ai.service.chat_runtime_coordinator import ChatCoordinationError
from plugins.ai.service.platform_chat_service import PlatformChatError, PlatformChatEvent
from utils.response_util import ResponseUtil

PLATFORM_SSE_MEDIA_TYPE = 'text/event-stream'
PLATFORM_SSE_HEADERS = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}

ai_chat_controller = APIRouterPro(
    prefix='/ai/chat',
    order_num=19,
    tags=['AI管理-平台对话'],
    dependencies=[PreAuthDependency(), UserInterfaceAuthDependency('ai:chat:list')],
)


def _user_id(current_user: CurrentUserModel) -> int:
    if current_user.user is None or current_user.user.user_id is None:
        raise AuditedHTTPException(
            status_code=401,
            detail={'code': 'CHAT_UNAUTHENTICATED', 'message': '用户未登录'},
            audit_code='CHAT_UNAUTHENTICATED',
            audit_message='用户未登录',
        )
    return int(current_user.user.user_id)


_platform_chat_service = get_platform_chat_service


def _public_error(error: PlatformChatError | ChatCoordinationError | ModelServiceError) -> AuditedHTTPException:
    detail = error.public_dict()
    return AuditedHTTPException(
        status_code=error.status_code,
        detail=detail,
        audit_code=detail['code'],
        audit_message=detail['message'],
    )


async def _call(operation: Any) -> Any:
    try:
        return await operation
    except (PlatformChatError, ChatCoordinationError, ModelServiceError) as error:
        raise _public_error(error) from None


def _serialize_platform_event(event: PlatformChatEvent) -> str:
    payload = json.dumps(dict(event.data), ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    return f'event: {event.event_type}\ndata: {payload}\n\n'


class _SerializedPlatformStream:
    """Keep the result closeable before the first serialized event is requested."""

    def __init__(self, stream: AsyncIterator[PlatformChatEvent]) -> None:
        self._stream = stream
        self._iterator = stream.__aiter__()

    def __aiter__(self) -> '_SerializedPlatformStream':
        return self

    async def __anext__(self) -> str:
        return _serialize_platform_event(await anext(self._iterator))

    async def aclose(self) -> None:
        await self._stream.aclose()


def _serialize_stream(stream: AsyncIterator[PlatformChatEvent]) -> _SerializedPlatformStream:
    return _SerializedPlatformStream(stream)


@ai_chat_controller.post(
    '/send',
    summary='发送平台对话消息',
    response_class=StreamingResponse,
    responses={200: {'content': {PLATFORM_SSE_MEDIA_TYPE: {}}}},
)
async def send_chat_message(
    request: Request,
    chat_req: PlatformChatRequestModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> StreamingResponse:
    stream = await _call(_platform_chat_service(request, query_db).send(_user_id(current_user), chat_req))
    return ManagedStreamingResponse(
        _serialize_stream(stream), media_type=PLATFORM_SSE_MEDIA_TYPE, headers=PLATFORM_SSE_HEADERS
    )


@ai_chat_controller.get(
    '/config',
    summary='获取平台对话偏好',
    response_model=DataResponseModel[PlatformChatPreferenceModel],
)
async def get_user_chat_config(
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await ChatPreferencesService(query_db).get_preferences(user_id=_user_id(current_user))
    return ResponseUtil.success(data=result)


@ai_chat_controller.patch(
    '/session/{session_id}',
    summary='重命名平台对话会话',
    response_model=DataResponseModel[PlatformSessionModel],
)
@Log(title='平台对话会话', business_type=BusinessType.UPDATE)
async def rename_chat_session(
    request: Request,
    session_id: Annotated[str, Path(description='Platform Session ID')],
    rename_req: PlatformSessionRenameRequestModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call(
        _platform_chat_service(request, query_db).rename_session(_user_id(current_user), session_id, rename_req.title)
    )
    return ResponseUtil.success(data=result)


@ai_chat_controller.put(
    '/config',
    summary='保存平台对话偏好',
    response_model=DataResponseModel[PlatformChatPreferenceModel],
)
@Log(
    title='平台对话偏好',
    business_type=BusinessType.UPDATE,
    request_log_mode='none',
    response_log_mode='none',
)
async def save_user_chat_config(
    config: PlatformChatPreferenceModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await ChatPreferencesService(query_db).update_preferences(
        user_id=_user_id(current_user), preferences=config, now=datetime.now()
    )
    return ResponseUtil.success(data=result)


@ai_chat_controller.patch(
    '/session/{session_id}/status',
    summary='归档或恢复平台对话会话',
    response_model=DataResponseModel[PlatformSessionModel],
)
@Log(title='平台对话会话', business_type=BusinessType.UPDATE)
async def set_chat_session_status(
    request: Request,
    session_id: Annotated[str, Path(description='Platform Session ID')],
    status_req: PlatformSessionStatusRequestModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call(
        _platform_chat_service(request, query_db).set_session_status(
            _user_id(current_user), session_id, status_req.status
        )
    )
    return ResponseUtil.success(data=result)


@ai_chat_controller.get(
    '/session/list',
    summary='获取平台对话会话列表',
    response_model=PageResponseModel[PlatformSessionModel],
)
async def get_chat_session_list(
    query: Annotated[PlatformSessionPageQueryModel, Query()],
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call(
        _platform_chat_service(request, query_db).list_sessions(
            _user_id(current_user), page_num=query.page_num, page_size=query.page_size, status=query.status
        )
    )
    return ResponseUtil.success(
        rows=result.rows,
        dict_content={
            'pageNum': result.page_num,
            'pageSize': result.page_size,
            'total': result.total,
            'hasNext': result.has_next,
        },
    )


@ai_chat_controller.get(
    '/session/{session_id}',
    summary='获取平台对话会话详情',
    response_model=DataResponseModel[PlatformSessionDetailModel],
)
async def get_chat_session_detail(
    request: Request,
    session_id: Annotated[str, Path(description='Platform Session ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call(
        _platform_chat_service(request, query_db).get_session_detail(_user_id(current_user), session_id)
    )
    return ResponseUtil.success(data=result)


@ai_chat_controller.delete(
    '/session/{session_id}',
    summary='永久删除平台对话会话',
    response_model=ResponseBaseModel,
)
@Log(title='平台对话会话', business_type=BusinessType.DELETE)
async def delete_chat_session(
    request: Request,
    session_id: Annotated[str, Path(description='Platform Session ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    await _call(_platform_chat_service(request, query_db).delete_session(_user_id(current_user), session_id))
    return ResponseUtil.success(msg='会话已删除')


@ai_chat_controller.post(
    '/cancel',
    summary='取消平台对话运行',
    response_model=ResponseBaseModel,
)
async def cancel_chat_run(
    request: Request,
    cancel_req: CancelRunRequestModel = Body(...),
    query_db: Annotated[AsyncSession, DBSessionDependency()] = None,
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()] = None,
) -> Response:
    if query_db is None or current_user is None:
        raise AuditedHTTPException(
            status_code=401,
            detail={'code': 'CHAT_UNAUTHENTICATED', 'message': '用户未登录'},
            audit_code='CHAT_UNAUTHENTICATED',
            audit_message='用户未登录',
        )
    await _call(_platform_chat_service(request, query_db).cancel_run(_user_id(current_user), cancel_req.run_id))
    return ResponseUtil.success(msg='取消请求已提交')
