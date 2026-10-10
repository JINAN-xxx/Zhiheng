import json
from collections.abc import AsyncIterator
from typing import Annotated

import anyio
from fastapi import Path, Query, Request, Response, params
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Receive, Scope, Send

from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.router import APIRouterPro
from module_admin.entity.vo.user_vo import CurrentUserModel
from plugins.agent_extension.controller.agent_controller import _actor, _call
from plugins.agent_extension.entity.vo.agent_vo import AgentId
from plugins.agent_extension.entity.vo.work_task_vo import (
    WorkTaskCreateModel,
    WorkTaskMemberAddModel,
    WorkTaskMemberUpdateModel,
    WorkTaskPageQueryModel,
    WorkTaskRunRequestModel,
    WorkTaskUpdateModel,
)
from plugins.agent_extension.infrastructure.work_task_provider import get_work_task_run_service
from plugins.agent_extension.service.agent_service import AgentService
from plugins.agent_extension.service.work_task_run_service import WorkTaskStream
from plugins.agent_extension.service.work_task_service import WorkTaskService
from utils.response_util import ResponseUtil

work_task_controller = APIRouterPro(
    prefix='/agent_extension/work-tasks', order_num=28, tags=['智能体扩展-工作任务'], dependencies=[PreAuthDependency()]
)
Database = Annotated[AsyncSession, DBSessionDependency()]
Actor = Annotated[CurrentUserModel, CurrentUserDependency()]
TaskId = Annotated[AgentId, Path()]


def permissions(action: str, *, use: bool = False) -> list[params.Depends]:
    values = [f'agent_extension:work:{action}']
    if use:
        values.append('agent_extension:agent:use')
    return [UserInterfaceAuthDependency(values, is_strict=True)]


def actor_args(user: CurrentUserModel) -> dict[str, int | bool]:
    actor_user_id, is_admin = _actor(user)
    return {'actor_user_id': actor_user_id, 'is_admin': is_admin}


@work_task_controller.get('', dependencies=permissions('list'))
async def list_tasks(
    query: Annotated[WorkTaskPageQueryModel, Query()], query_db: Database, current_user: Actor
) -> Response:
    result = await _call(WorkTaskService(query_db).list_tasks(query, **actor_args(current_user)))
    return ResponseUtil.success(dict_content=result)


@work_task_controller.post('', dependencies=permissions('add', use=True))
async def create_task(payload: WorkTaskCreateModel, query_db: Database, current_user: Actor) -> Response:
    return ResponseUtil.success(
        data=await _call(WorkTaskService(query_db).create_task(payload, **actor_args(current_user)))
    )


@work_task_controller.get('/available-agents', dependencies=[UserInterfaceAuthDependency('agent_extension:agent:use')])
async def available_agents(query_db: Database, current_user: Actor) -> Response:
    return ResponseUtil.success(
        data=await _call(AgentService(query_db).list_usable_published_agents(**actor_args(current_user)))
    )


@work_task_controller.get('/{task_id}', dependencies=permissions('query'))
async def get_task(task_id: TaskId, query_db: Database, current_user: Actor) -> Response:
    return ResponseUtil.success(
        data=await _call(WorkTaskService(query_db).get_task(task_id, **actor_args(current_user)))
    )


@work_task_controller.put('/{task_id}', dependencies=permissions('edit'))
async def update_task(
    task_id: TaskId, payload: WorkTaskUpdateModel, query_db: Database, current_user: Actor
) -> Response:
    return ResponseUtil.success(
        data=await _call(WorkTaskService(query_db).update_task(task_id, payload, **actor_args(current_user)))
    )


@work_task_controller.delete('/{task_id}', dependencies=permissions('remove'))
async def delete_task(task_id: TaskId, query_db: Database, current_user: Actor) -> Response:
    await _call(WorkTaskService(query_db).delete_task(task_id, **actor_args(current_user)))
    return ResponseUtil.success()


@work_task_controller.post('/{task_id}/members', dependencies=permissions('edit', use=True))
async def add_member(
    task_id: TaskId, payload: WorkTaskMemberAddModel, query_db: Database, current_user: Actor
) -> Response:
    return ResponseUtil.success(
        data=await _call(WorkTaskService(query_db).add_member(task_id, payload, **actor_args(current_user)))
    )


@work_task_controller.put('/{task_id}/members/{agent_id}', dependencies=permissions('edit', use=True))
async def update_member(
    task_id: TaskId,
    agent_id: Annotated[AgentId, Path()],
    payload: WorkTaskMemberUpdateModel,
    query_db: Database,
    current_user: Actor,
) -> Response:
    return ResponseUtil.success(
        data=await _call(
            WorkTaskService(query_db).update_member(task_id, agent_id, payload, **actor_args(current_user))
        )
    )


@work_task_controller.delete('/{task_id}/members/{agent_id}', dependencies=permissions('edit'))
async def remove_member(
    task_id: TaskId,
    agent_id: Annotated[AgentId, Path()],
    query_db: Database,
    current_user: Actor,
    expected_version: Annotated[int, Query(alias='expectedVersion', ge=1)],
) -> Response:
    return ResponseUtil.success(
        data=await _call(
            WorkTaskService(query_db).remove_member(task_id, agent_id, expected_version, **actor_args(current_user))
        )
    )


class WorkStreamingResponse(StreamingResponse):
    def __init__(self, stream: WorkTaskStream) -> None:
        self.work_stream = stream
        super().__init__(
            self._events(),
            media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )

    async def _events(self) -> AsyncIterator[bytes]:
        try:
            async for event in self.work_stream:
                yield f'event: {event.event_type}\ndata: {json.dumps(dict(event.data), ensure_ascii=False)}\n\n'.encode()
        finally:
            with anyio.CancelScope(shield=True):
                await self.work_stream.aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.work_stream.aclose()


@work_task_controller.post('/{task_id}/runs', dependencies=permissions('run', use=True))
async def run_task(
    task_id: TaskId, payload: WorkTaskRunRequestModel, request: Request, current_user: Actor
) -> Response:
    stream = await _call(get_work_task_run_service(request).start(task_id, payload, **actor_args(current_user)))
    return WorkStreamingResponse(stream)


@work_task_controller.post('/{task_id}/runs/{run_id}/cancel', dependencies=permissions('run'))
async def cancel_run(
    task_id: TaskId, run_id: Annotated[AgentId, Path()], request: Request, current_user: Actor
) -> Response:
    await _call(get_work_task_run_service(request).cancel(task_id, run_id, **actor_args(current_user)))
    return ResponseUtil.success()
