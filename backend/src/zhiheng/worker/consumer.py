"""ARQ lifecycle adapter: retain shielded Redis finalizers until shutdown joins them."""

import asyncio
import signal
from typing import Any

from arq import Worker


class ConsumerWorker(Worker):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._finalizers: set[asyncio.Task[None]] = set()
        self._finalizer_errors: list[BaseException] = []

    def _register_finalizer(self) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._finalizers.add(task)
        task.add_done_callback(self._finalizer_done)

    def _finalizer_done(self, task: asyncio.Task[None]) -> None:
        self._finalizers.discard(task)
        error = asyncio.CancelledError() if task.cancelled() else task.exception()
        if error is not None:
            self._finalizer_errors.append(error)

    async def finish_job(
        self,
        job_id: str,
        finish: bool,
        result_data: bytes | None,
        result_timeout_s: float | None,
        keep_result_forever: bool,
        incr_score: int | None,
        keep_in_progress: float | None,
    ) -> None:
        self._register_finalizer()
        await super().finish_job(
            job_id, finish, result_data, result_timeout_s, keep_result_forever, incr_score, keep_in_progress
        )

    async def finish_failed_job(self, job_id: str, result_data: bytes | None) -> None:
        self._register_finalizer()
        await super().finish_failed_job(job_id, result_data)

    async def wait_for_finalizers(self) -> list[BaseException]:
        while self._finalizers:
            await asyncio.gather(*tuple(self._finalizers), return_exceptions=True)
        errors, self._finalizer_errors = self._finalizer_errors, []
        return errors

    async def close(self) -> None:
        self.handle_sig(signal.SIGUSR1)
        # ARQ's gather raises at the first cancellation; join all writers first.
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        errors = await self.wait_for_finalizers()
        # Cancellation before ARQ's run_job try block leaves no finalizer to run.
        # Its durable pending Outbox still controls recovery, without automatic retries.
        self.tasks = {job_id: task for job_id, task in self.tasks.items() if not task.cancelled()}
        await super().close()
        if errors:
            raise errors[0]
