import asyncio
from collections.abc import AsyncIterator
from time import monotonic
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from zhiheng.api.dependencies import COOKIE_NAME, ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/runs', tags=['runs'])
SSE_HEARTBEAT_SECONDS = 10


@router.get('/{run_id}')
async def run_snapshot(
    run_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> RunSnapshot:
    return await container.runs.snapshot(session, run_id, principal.id)


@router.post('/{run_id}/cancel')
async def cancel_run(
    run_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> RunSnapshot:
    async with session.begin():
        return await container.admission.cancel(session, principal, run_id)


@router.get('/{run_id}/events')
async def events(
    run_id: UUID, request: Request, container: ContainerDependency, principal: PrincipalDependency
) -> StreamingResponse:
    async with container.database.sessions() as session:
        await container.runs.snapshot(session, run_id, principal.id)

    async def snapshots() -> AsyncIterator[str]:
        revision = -1
        last_emit = monotonic()
        while not await request.is_disconnected():
            try:
                async with container.database.sessions.begin() as session:
                    current = await container.identity.authenticate(
                        session, request.cookies.get(COOKIE_NAME), lock=True
                    )
                    value = await container.runs.snapshot(session, run_id, current.id)
            except AppError as exc:
                if exc.code == 'unauthenticated':
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
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
        },
    )
