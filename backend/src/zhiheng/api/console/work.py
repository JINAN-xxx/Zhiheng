import asyncio
from collections.abc import AsyncIterator
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse

from zhiheng.api.common.dependencies import COOKIE_NAME, ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.agents.public import AgentSummary
from zhiheng.modules.knowledge.public import PreparationInfo
from zhiheng.modules.work.public import AddMember, CreateTask, TaskDetail, TaskPage, UpdateTask, UpgradeMember, WorkTurn
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability

router = APIRouter(prefix='/api/work', tags=['work'])
SSE_HEARTBEAT_SECONDS = 10
PositiveDecimalQuery = Annotated[str, Query(pattern=r'^[1-9][0-9]{0,9}$')]
PageSizeQuery = Annotated[str, Query(pattern=r'^(?:[1-9][0-9]?|100)$')]


@router.get('/agents/available')
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
        item.preparation_id = await container.knowledge_preparation.store.current(session, 'work_turn', item.id)
    return value


@router.post('/tasks', status_code=201)
async def create(
    body: CreateTask, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> TaskDetail:
    async with session.begin():
        return await container.work_runs.create(session, principal, body)


@router.get('/tasks/{task_id}')
async def detail(
    task_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> TaskDetail:
    return await container.work_runs.detail(session, principal, task_id)


@router.patch('/tasks/{task_id}')
async def update(
    task_id: UUID,
    body: UpdateTask,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> TaskDetail:
    async with session.begin():
        return await container.work_runs.update(session, principal, task_id, body)


@router.delete('/tasks/{task_id}', status_code=204)
async def remove(
    task_id: UUID,
    expected_version: PositiveDecimalQuery,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.work_runs.remove(session, principal, task_id, int(expected_version))
    return Response(status_code=204)


@router.post('/tasks/{task_id}/members')
async def add_member(
    task_id: UUID,
    body: AddMember,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> TaskDetail:
    async with session.begin():
        return await container.work_runs.change_member(
            session, principal, task_id, body.agent_id, body.expected_version
        )


@router.patch('/tasks/{task_id}/members/{agent_id}')
async def upgrade_member(
    task_id: UUID,
    agent_id: UUID,
    body: UpgradeMember,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> TaskDetail:
    async with session.begin():
        return await container.work_runs.change_member(
            session, principal, task_id, agent_id, body.expected_version, body.expected_published_version
        )


@router.delete('/tasks/{task_id}/members/{agent_id}')
async def remove_member(
    task_id: UUID,
    agent_id: UUID,
    expected_version: PositiveDecimalQuery,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> TaskDetail:
    async with session.begin():
        await container.work.require(session, principal, task_id, lock=True)
        await container.work_runs.ensure_preparation_idle(session, task_id)
        await container.work.change_member(session, principal, task_id, int(expected_version), agent_id, None)
        return await container.work_runs.detail(session, principal, task_id)


@router.post('/tasks/{task_id}/runs', status_code=202)
async def admit(
    task_id: UUID,
    body: WorkTurn,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot | PreparationInfo:
    async with session.begin():
        existing = await container.work_runs.replay(session, principal, task_id, body)
        if existing:
            return existing
        knowledge = await container.work_runs.requires_knowledge(session, principal, task_id, body.agent_id)
    if knowledge:
        return await container.work_runs.prepare_knowledge_work(principal, task_id, body)
    async with session.begin():
        return await container.work_runs.admit(session, principal, task_id, body, container.settings.token_limit)


@router.get('/tasks/{task_id}/runs/{run_id}')
async def snapshot(
    task_id: UUID,
    run_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot:
    return await container.work_runs.snapshot(session, principal, task_id, run_id)


@router.post('/tasks/{task_id}/runs/{run_id}/cancel')
async def cancel(
    task_id: UUID,
    run_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot:
    async with session.begin():
        return await container.work_runs.cancel(session, principal, task_id, run_id)


@router.get('/tasks/{task_id}/runs/{run_id}/events')
async def events(
    task_id: UUID, run_id: UUID, request: Request, container: ContainerDependency, principal: PrincipalDependency
) -> StreamingResponse:
    async with container.database.sessions() as session:
        await container.work_runs.snapshot(session, principal, task_id, run_id)

    async def snapshots() -> AsyncIterator[str]:
        revision, last_emit = -1, monotonic()
        while not await request.is_disconnected():
            try:
                async with container.database.sessions.begin() as session:
                    current = await container.identity.authenticate(
                        session, request.cookies.get(COOKIE_NAME), lock=True
                    )
                    require_capability(current, 'work', 'read')
                    value = await container.work_runs.snapshot(session, current, task_id, run_id)
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
