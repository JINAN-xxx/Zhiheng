from collections.abc import Awaitable
from typing import Annotated, TypeVar

from fastapi import Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from common.annotation.log_annotation import AuditedHTTPException
from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.router import APIRouterPro
from common.vo import DataResponseModel, PageResponseModel, ResponseBaseModel
from module_admin.entity.vo.user_vo import CurrentUserModel
from plugins.agent_extension.config import AgentSkillSettings
from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.agent_extension.entity.vo.agent_vo import (
    AgentConfigModel,
    AgentDetailModel,
    AgentId,
    AgentPageQueryModel,
    AgentPublishModel,
    AgentUpdateModel,
)
from plugins.agent_extension.service.agent_service import AgentService
from utils.response_util import ResponseUtil

ResultT = TypeVar('ResultT')

agent_controller = APIRouterPro(
    prefix='/agent_extension/agents',
    order_num=26,
    tags=['Agent Extension - Agents'],
    dependencies=[PreAuthDependency()],
)


def _actor(current_user: CurrentUserModel) -> tuple[int, bool]:
    user = current_user.user
    if user is None or user.user_id is None or not user.user_name:
        raise AuditedHTTPException(
            status_code=401,
            detail={'code': 'AGENT_UNAUTHENTICATED', 'message': 'Authentication required'},
            audit_code='AGENT_UNAUTHENTICATED',
            audit_message='Authentication required',
        )
    return user.user_id, bool(user.admin)


def _agent_service(query_db: AsyncSession) -> AgentService:
    return AgentService(query_db, max_selected_skills=AgentSkillSettings().max_selected)


def _require_skill_use(current_user: CurrentUserModel, slugs: list[str]) -> None:
    if slugs and not {'*:*:*', 'agent_extension:skill:use'}.intersection(current_user.permissions):
        raise AuditedHTTPException(
            status_code=403,
            detail={'code': 'AGENT_SKILL_FORBIDDEN', 'message': 'Skill use permission required'},
            audit_code='AGENT_SKILL_FORBIDDEN',
            audit_message='Skill use permission required',
        )


async def _call(operation: Awaitable[ResultT]) -> ResultT:
    try:
        return await operation
    except AgentServiceError as error:
        raise AuditedHTTPException(
            status_code=error.status_code,
            detail=error.public_dict(),
            audit_code=error.code,
            audit_message=error.message,
        ) from None


@agent_controller.get(
    '',
    response_model=PageResponseModel[AgentDetailModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:agent:list')],
)
async def list_agents(
    query: Annotated[AgentPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    actor_user_id, is_admin = _actor(current_user)
    result = await _call(
        _agent_service(query_db).list_agents(
            query,
            actor_user_id=actor_user_id,
            is_admin=is_admin,
            is_page=True,
        )
    )
    return ResponseUtil.success(model_content=result)


@agent_controller.post(
    '',
    response_model=DataResponseModel[AgentDetailModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:agent:add')],
)
async def create_agent(
    payload: AgentConfigModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    actor_user_id, _ = _actor(current_user)
    _require_skill_use(current_user, payload.skill_slugs)
    result = await _call(_agent_service(query_db).create_agent(payload, actor_user_id=actor_user_id))
    return ResponseUtil.success(data=result)


@agent_controller.get(
    '/{agent_id}',
    response_model=DataResponseModel[AgentDetailModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:agent:query')],
)
async def get_agent(
    agent_id: Annotated[AgentId, Path()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    actor_user_id, is_admin = _actor(current_user)
    result = await _call(_agent_service(query_db).get_agent(agent_id, actor_user_id=actor_user_id, is_admin=is_admin))
    return ResponseUtil.success(data=result)


@agent_controller.put(
    '/{agent_id}',
    response_model=DataResponseModel[AgentDetailModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:agent:edit')],
)
async def update_agent(
    agent_id: Annotated[AgentId, Path()],
    payload: AgentUpdateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    actor_user_id, is_admin = _actor(current_user)
    _require_skill_use(current_user, payload.skill_slugs)
    result = await _call(
        _agent_service(query_db).update_agent(
            agent_id,
            payload,
            actor_user_id=actor_user_id,
            is_admin=is_admin,
        )
    )
    return ResponseUtil.success(data=result)


@agent_controller.delete(
    '/{agent_id}',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('agent_extension:agent:remove')],
)
async def delete_agent(
    agent_id: Annotated[AgentId, Path()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    actor_user_id, is_admin = _actor(current_user)
    await _call(_agent_service(query_db).delete_agent(agent_id, actor_user_id=actor_user_id, is_admin=is_admin))
    return ResponseUtil.success()


@agent_controller.post(
    '/{agent_id}/publish',
    response_model=DataResponseModel[AgentDetailModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:agent:publish')],
)
async def publish_agent(
    agent_id: Annotated[AgentId, Path()],
    payload: AgentPublishModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    actor_user_id, is_admin = _actor(current_user)
    service = _agent_service(query_db)
    draft = await _call(service.get_agent(agent_id, actor_user_id=actor_user_id, is_admin=is_admin))
    _require_skill_use(current_user, draft.skill_slugs)
    result = await _call(service.publish_agent(agent_id, payload, actor_user_id=actor_user_id, is_admin=is_admin))
    return ResponseUtil.success(data=result)


@agent_controller.post(
    '/{agent_id}/unpublish',
    response_model=DataResponseModel[AgentDetailModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:agent:publish')],
)
async def unpublish_agent(
    agent_id: Annotated[AgentId, Path()],
    payload: AgentPublishModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    actor_user_id, is_admin = _actor(current_user)
    result = await _call(
        _agent_service(query_db).unpublish_agent(
            agent_id,
            payload,
            actor_user_id=actor_user_id,
            is_admin=is_admin,
        )
    )
    return ResponseUtil.success(data=result)
