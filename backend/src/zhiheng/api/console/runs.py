from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from zhiheng.api.common.dependencies import COOKIE_NAME, ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.api.common.run_polling import console_run_events
from zhiheng.runtime.contracts import RunSnapshot

router = APIRouter(prefix='/api/runs', tags=['runs'])


@router.get('/{run_id}')
async def run_snapshot(
    run_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> RunSnapshot:
    value = await container.runs.snapshot(session, run_id, principal.id)
    await container.run_content.authorize_read(session, principal, value)
    return value


@router.post('/{run_id}/cancel')
async def cancel_run(
    run_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> RunSnapshot:
    async with session.begin():
        value = await container.runs.snapshot(session, run_id, principal.id)
        await container.run_content.authorize_read(session, principal, value)
        return await container.admission.cancel(session, principal, run_id)


@router.get('/{run_id}/events')
async def events(
    run_id: UUID, request: Request, container: ContainerDependency, principal: PrincipalDependency
) -> StreamingResponse:
    async with container.database.sessions() as session:
        initial = await container.runs.snapshot(session, run_id, principal.id)
        await container.run_content.authorize_read(session, principal, initial)

    async def read() -> RunSnapshot:
        async with container.database.sessions.begin() as session:
            current = await container.identity.authenticate(session, request.cookies.get(COOKIE_NAME), lock=True)
            value = await container.runs.snapshot(session, run_id, current.id)
            await container.run_content.authorize_read(session, current, value)
            return value

    return console_run_events(request, read, closed_statuses=frozenset({403, 404, 409}))
