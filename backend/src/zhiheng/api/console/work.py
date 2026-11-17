"""Owned workspace tasks, durable execution and managed-file transport."""

import asyncio
from collections.abc import AsyncIterator
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse

from zhiheng.api.common.dependencies import COOKIE_NAME, ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.api.common.file_content import file_content_response
from zhiheng.modules.agents.public import AgentSummary
from zhiheng.modules.work.public import (
    AddMember,
    AgentTurn,
    CreateAgentTask,
    TaskDetail,
    TaskPage,
    UpdateAgentTask,
    UpgradeMember,
    WorkOptions,
)
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability

router = APIRouter(prefix='/api/work', tags=['work'])
SSE_HEARTBEAT_SECONDS = 10
PositiveDecimalQuery = Annotated[str, Query(pattern=r'^[1-9][0-9]{0,9}$')]
PageSizeQuery = Annotated[str, Query(pattern=r'^(?:[1-9][0-9]?|100)$')]
PREVIEW_MIMES = frozenset(
    {'application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'text/plain', 'text/markdown', 'text/csv'}
)


@router.get('/options')
async def options(container: ContainerDependency, principal: PrincipalDependency) -> WorkOptions:
    require_capability(principal, 'work', 'read')
    return await container.work_agent.options(principal)


@router.get('/agents/available', deprecated=True)
async def available(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    task_id: UUID | None = None,
) -> list[AgentSummary]:
    return await container.work_runs.available(session, principal, task_id)


@router.get('/tasks')
async def tasks(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: PositiveDecimalQuery = '1',
    page_size: PageSizeQuery = '20',
    search: str = '',
    status: Literal['active', 'completed', 'archived'] | None = None,
) -> TaskPage:
    value = await container.work.list_tasks(
        session, principal, page=int(page), page_size=int(page_size), search=search, status=status
    )
    for item in value.items:
        if item.mode == 'legacy':
            item.preparation_id = await container.knowledge_preparation.store.current(session, 'work_turn', item.id)
    return value


@router.post('/tasks', status_code=201)
async def create(body: CreateAgentTask, container: ContainerDependency, principal: PrincipalDependency) -> TaskDetail:
    require_capability(principal, 'work', 'create')
    return await container.work_agent.create(principal, body)


@router.get('/tasks/{task_id}')
async def detail(
    task_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> TaskDetail:
    task = await container.work.require(session, principal, task_id)
    if task.mode == 'legacy':
        return await container.work_runs.detail(session, principal, task_id)
    return await container.work_agent.detail(principal, task_id)


@router.patch('/tasks/{task_id}')
async def update(
    task_id: UUID, body: UpdateAgentTask, container: ContainerDependency, principal: PrincipalDependency
) -> TaskDetail:
    require_capability(principal, 'work', 'update')
    return await container.work_agent.update(principal, task_id, body)


@router.delete('/tasks/{task_id}', status_code=204)
async def remove(
    task_id: UUID,
    expected_version: PositiveDecimalQuery,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    task = await container.work.require(session, principal, task_id)
    if task.mode == 'legacy':
        await session.rollback()
        async with session.begin():
            await container.work_runs.remove(session, principal, task_id, int(expected_version))
    else:
        await container.work_agent.remove(principal, task_id, int(expected_version))
    return Response(status_code=204)


async def legacy_read_only(
    task_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> None:
    await container.work.require(session, principal, task_id)
    raise AppError('work_legacy_read_only', '历史成员任务只读，请新建任务继续处理', 409)


@router.post('/tasks/{task_id}/members', deprecated=True)
async def add_member(
    task_id: UUID,
    body: AddMember,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> TaskDetail:
    await legacy_read_only(task_id, container, principal, session)
    raise AssertionError('unreachable')


@router.patch('/tasks/{task_id}/members/{agent_id}', deprecated=True)
async def upgrade_member(
    task_id: UUID,
    agent_id: UUID,
    body: UpgradeMember,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> TaskDetail:
    await legacy_read_only(task_id, container, principal, session)
    raise AssertionError('unreachable')


@router.delete('/tasks/{task_id}/members/{agent_id}', deprecated=True)
async def remove_member(
    task_id: UUID,
    agent_id: UUID,
    expected_version: PositiveDecimalQuery,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> TaskDetail:
    await legacy_read_only(task_id, container, principal, session)
    raise AssertionError('unreachable')


@router.post('/tasks/{task_id}/files', status_code=201, openapi_extra={'x-transport-policy': 'binary_file'})
async def upload(
    task_id: UUID,
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> TaskDetail:
    require_capability(principal, 'work', 'use')
    return await container.work_agent.upload(principal, task_id, filename, await request.body())


@router.delete('/tasks/{task_id}/inputs/{file_id}')
async def detach_input(
    task_id: UUID,
    file_id: UUID,
    expected_version: PositiveDecimalQuery,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> TaskDetail:
    require_capability(principal, 'work', 'update')
    return await container.work_agent.detach_input(principal, task_id, file_id, int(expected_version))


@router.get('/tasks/{task_id}/inputs/{file_id}/download')
async def input_download(
    task_id: UUID, file_id: UUID, request: Request, container: ContainerDependency, principal: PrincipalDependency
) -> Response:
    require_capability(principal, 'work', 'read')
    info, content = await container.work_agent.download_input(principal, task_id, file_id)
    return await file_content_response(request, container, principal, info, content)


@router.get('/tasks/{task_id}/artifacts/{artifact_id}/download')
async def artifact_download(
    task_id: UUID, artifact_id: UUID, request: Request, container: ContainerDependency, principal: PrincipalDependency
) -> Response:
    require_capability(principal, 'work', 'read')
    info, content = await container.work_agent.download_artifact(principal, task_id, artifact_id)
    return await file_content_response(request, container, principal, info, content)


@router.get('/tasks/{task_id}/artifacts/{artifact_id}/preview')
async def artifact_preview(
    task_id: UUID, artifact_id: UUID, request: Request, container: ContainerDependency, principal: PrincipalDependency
) -> Response:
    require_capability(principal, 'work', 'read')
    info, content = await container.work_agent.download_artifact(principal, task_id, artifact_id, preview=True)
    if info.mime not in PREVIEW_MIMES:
        raise AppError('work_preview_unavailable', '此成果暂不支持预览，请下载查看', 415)
    response = await file_content_response(request, container, principal, info, content)
    response.headers['Content-Type'] = info.mime + ('; charset=utf-8' if info.mime.startswith('text/') else '')
    response.headers['Content-Disposition'] = 'inline'
    return response


@router.post('/tasks/{task_id}/runs', status_code=202)
async def admit(
    task_id: UUID, body: AgentTurn, container: ContainerDependency, principal: PrincipalDependency
) -> RunSnapshot:
    require_capability(principal, 'work', 'use')
    return await container.work_agent.admit(principal, task_id, body)


@router.get('/tasks/{task_id}/runs/{run_id}')
async def snapshot(
    task_id: UUID,
    run_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot:
    task = await container.work.require(session, principal, task_id)
    if task.mode == 'legacy':
        return await container.work_runs.snapshot(session, principal, task_id, run_id)
    return await container.work_agent.snapshot(principal, task_id, run_id)


@router.post('/tasks/{task_id}/runs/{run_id}/cancel')
async def cancel(
    task_id: UUID,
    run_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot:
    require_capability(principal, 'work', 'use')
    task = await container.work.require(session, principal, task_id)
    if task.mode == 'legacy':
        await session.rollback()
        async with session.begin():
            return await container.work_runs.cancel(session, principal, task_id, run_id)
    return await container.work_agent.cancel(principal, task_id, run_id)


@router.get('/tasks/{task_id}/runs/{run_id}/events')
async def events(
    task_id: UUID, run_id: UUID, request: Request, container: ContainerDependency, principal: PrincipalDependency
) -> StreamingResponse:
    async with container.database.sessions() as session:
        task = await container.work.require(session, principal, task_id)
        if task.mode == 'legacy':
            await container.work_runs.snapshot(session, principal, task_id, run_id)
        else:
            await container.work_agent.snapshot_in(session, principal, task_id, run_id)

    async def snapshots() -> AsyncIterator[str]:
        revision, last_emit = -1, monotonic()
        while not await request.is_disconnected():
            try:
                async with container.database.sessions.begin() as session:
                    current = await container.identity.authenticate(
                        session, request.cookies.get(COOKIE_NAME), lock=True
                    )
                    require_capability(current, 'work', 'read')
                    if task.mode == 'legacy':
                        value = await container.work_runs.snapshot(session, current, task_id, run_id)
                    else:
                        value = await container.work_agent.snapshot_in(session, current, task_id, run_id)
            except AppError as error:
                if error.code == 'unauthenticated' or error.status_code in {403, 404}:
                    return
                raise
            if value.revision != revision:
                yield f'event: snapshot\ndata: {value.model_dump_json()}\n\n'
                revision, last_emit = value.revision, monotonic()
            if value.status in TERMINAL_STATUSES:
                return
            if monotonic() - last_emit > SSE_HEARTBEAT_SECONDS:
                yield ': heartbeat\n\n'
                last_emit = monotonic()
            await asyncio.sleep(0.15)

    return StreamingResponse(
        snapshots(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'},
    )
