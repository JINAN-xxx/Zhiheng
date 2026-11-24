"""Bound database polling when a run is queued or its projection is unchanged."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from time import monotonic

from fastapi import Request
from fastapi.responses import StreamingResponse

from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.shared.errors import AppError

SSE_HEARTBEAT_SECONDS = 10


class RunPollInterval:
    def __init__(self, minimum: float) -> None:
        self.minimum = minimum
        self.delay = minimum
        self.revision: int | None = None

    def next_delay(self, snapshot: RunSnapshot) -> float:
        changed = snapshot.revision != self.revision
        self.revision = snapshot.revision
        if snapshot.status == 'pending':
            self.delay = min(1.0, max(0.25, self.delay * 2))
        else:
            self.delay = self.minimum if changed else min(0.6, self.delay * 2)
        return self.delay


def console_run_events(
    request: Request,
    read: Callable[[], Awaitable[RunSnapshot]],
    *,
    closed_statuses: frozenset[int] = frozenset({403, 404}),
) -> StreamingResponse:
    async def snapshots() -> AsyncIterator[str]:
        interval = RunPollInterval(0.15)
        last_emit = monotonic()
        while not await request.is_disconnected():
            try:
                snapshot = await read()
            except AppError as error:
                if error.code == 'unauthenticated' or error.status_code in closed_statuses:
                    return
                raise
            changed = snapshot.revision != interval.revision
            delay = interval.next_delay(snapshot)
            if changed:
                yield f'event: snapshot\ndata: {snapshot.model_dump_json()}\n\n'
                last_emit = monotonic()
            if snapshot.status in TERMINAL_STATUSES:
                return
            if monotonic() - last_emit > SSE_HEARTBEAT_SECONDS:
                yield ': heartbeat\n\n'
                last_emit = monotonic()
            await asyncio.sleep(delay)

    return StreamingResponse(
        snapshots(),
        media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no'},
    )
