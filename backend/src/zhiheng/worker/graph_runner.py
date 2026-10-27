"""Explicit lifecycle owner of a bounded set of SDK attempts; ARQ slots stay free."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

from zhiheng.application.graph_jobs import GraphJobs
from zhiheng.shared.errors import AppError


class GraphRunner:
    def __init__(self, jobs: GraphJobs, limit: int, publish: Callable[[], Awaitable[None]]) -> None:
        self.jobs, self.limit, self.publish = jobs, limit, publish
        self.tasks: dict[UUID, asyncio.Task[None]] = {}
        self.poller: asyncio.Task[None] | None = None
        self.stopping = False

    async def start(self) -> None:
        if self.poller is None:
            self.poller = asyncio.create_task(self._poll(), name='graph-supervisor')

    async def _poll(self) -> None:
        try:
            while not self.stopping:
                for job_id, task in tuple(self.tasks.items()):
                    if task.done():
                        task.result()
                        del self.tasks[job_id]
                async with self.jobs.database.sessions.begin() as session:
                    await self.jobs.store.recover(session)
                    pending = await self.jobs.store.pending(session)
                    garbage = await self.jobs.store.gc_candidates(session)
                for job_id in pending:
                    if len(self.tasks) >= self.limit:
                        break
                    if job_id not in self.tasks:
                        self.tasks[job_id] = asyncio.create_task(self.jobs.execute(job_id), name=f'graph-{job_id}')
                for revision in garbage:
                    await self.jobs.gc(revision)
                await self.publish()
                await asyncio.sleep(0.1)
        finally:
            for task in self.tasks.values():
                task.cancel()
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
            self.tasks.clear()

    def require_running(self) -> None:
        if self.stopping or self.poller is None or self.poller.done():
            if self.poller is not None and self.poller.done() and not self.poller.cancelled():
                self.poller.exception()
            raise AppError('graph_supervisor_stopped', '图谱任务监督已停止', 503)

    async def close(self) -> None:
        self.stopping = True
        if self.poller is not None:
            self.poller.cancel()
            while not self.poller.done():
                try:
                    await asyncio.shield(self.poller)
                except asyncio.CancelledError:  # noqa: PERF203 - shutdown waits through repeated cancellation
                    continue
                except Exception:
                    break
            if not self.poller.cancelled():
                self.poller.exception()
            self.poller = None
        await self.jobs.sdk.close()
