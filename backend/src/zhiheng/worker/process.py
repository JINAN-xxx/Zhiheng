"""One lifecycle owner supervises independent bounded consumers and coordinators."""

import asyncio
import logging
import signal
from time import time
from typing import TYPE_CHECKING, cast

from arq import Worker
from arq.connections import ArqRedis, RedisSettings, create_pool

from zhiheng.application.execution.failures import database_sqlstate, exception_frames
from zhiheng.bootstrap.container import Container
from zhiheng.worker.consumer import ConsumerWorker
from zhiheng.worker.graph_runner import GraphRunner
from zhiheng.worker.maintenance import file_maintenance, maintain
from zhiheng.worker.publisher import publish_knowledge, publish_outbox

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

logger = logging.getLogger(__name__)
QUIET_PASSES = 2


class WorkerProcess:
    def __init__(self, container: Container, consumers: tuple[Worker, ...], *, burst: bool) -> None:
        self.container, self.consumers, self.burst = container, consumers, burst
        self.ctx: dict[str, object] = {'container': container}
        self._publisher_pool: ArqRedis | None = None
        self._runner: GraphRunner | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._run_task: asyncio.Task[object] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._stop, self._dispatch_requested = asyncio.Event(), asyncio.Event()
        self._dispatch_passes = self._maintenance_passes = 0
        self._signals: list[tuple[signal.Signals, object, object]] = []
        self._closing = False

    async def async_run(self) -> None:
        if self._stop.is_set():
            return
        if self._run_task is not None:
            raise RuntimeError('Worker process is already running')
        self._run_task = asyncio.current_task()
        try:
            if not self.burst:
                self._install_signals()
            await self.container.database.require_schema()
            self._publisher_pool = await create_pool(
                RedisSettings.from_dsn(self.container.settings.redis_url.get_secret_value()),
                default_queue_name=self.container.settings.worker_queue,
            )
            self.ctx['redis'] = self._publisher_pool
            self._runner = GraphRunner(
                self.container.graph_jobs, self.container.settings.graph_runner_limit, self._wake_dispatch
            )
            self.ctx['graph_runner'] = self._runner
            await self._runner.start()
            for number, consumer in enumerate(self.consumers):
                self._tasks.append(asyncio.create_task(consumer.async_run(), name=f'worker-consumer-{number}'))
            self._tasks.extend(
                (
                    asyncio.create_task(self._dispatch(), name='worker-dispatch'),
                    asyncio.create_task(self._heartbeat(), name='worker-heartbeat'),
                    asyncio.create_task(self._maintenance(), name='worker-maintenance'),
                    asyncio.create_task(self._parsing(), name='worker-document-parsing'),
                )
            )
            if self._runner.poller is None:
                raise RuntimeError('Graph runner did not start')
            self._tasks.append(self._runner.poller)
            stopping = asyncio.create_task(self._wait_stop(), name='worker-stop')
            self._tasks.append(stopping)
            draining = asyncio.create_task(self._drain(), name='worker-drain') if self.burst else None
            if draining is not None:
                self._tasks.append(draining)
            done, _ = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task not in {stopping, draining}:
                    task.result()
                    raise RuntimeError(f'{task.get_name()} stopped unexpectedly')
            for task in done:
                task.result()
        finally:
            await self.close()

    async def _wake_dispatch(self) -> None:
        self._dispatch_requested.set()

    async def _dispatch(self) -> None:
        assert self._publisher_pool is not None
        while True:
            self._dispatch_requested.clear()
            await publish_outbox(self.container, self._publisher_pool)
            await publish_knowledge(self.container, self._publisher_pool)
            self._dispatch_passes += 1
            try:
                await asyncio.wait_for(self._dispatch_requested.wait(), timeout=0.1)
            except asyncio.TimeoutError:
                pass

    async def _parsing(self) -> None:
        while True:
            try:
                await self.container.document_parsing.tick()
            except Exception as error:
                logger.error('Document parsing tick failed error_type=%s', type(error).__name__)
            await asyncio.sleep(0.5)

    async def _heartbeat(self) -> None:
        while True:
            await self.container.redis.set(self.container.settings.worker_health_key, str(time()), ex=15)
            await asyncio.sleep(2)

    async def _maintenance(self) -> None:
        # Preserve the old quarter-hour plus twenty seconds file sweep schedule.
        next_files = self._next_file_sweep()
        while True:
            await maintain(self.container)
            self._maintenance_passes += 1
            if not self.burst and time() >= next_files:
                try:
                    await asyncio.wait_for(file_maintenance(self.container), timeout=180)
                except Exception as error:
                    # FileJobs owns and records this attempt's failure. A housekeeping
                    # failure must not interrupt unrelated model calls or retry this sweep.
                    logger.error(
                        'File maintenance failed error_type=%s sqlstate=%s frames=%s',
                        type(error).__name__,
                        database_sqlstate(error),
                        exception_frames(error),
                    )
                next_files = self._next_file_sweep()
            await asyncio.sleep(1)

    @staticmethod
    def _next_file_sweep() -> float:
        now = time()
        upcoming = (int(now) // 900) * 900 + 20
        return float(upcoming if upcoming > now else upcoming + 900)

    async def _wait_stop(self) -> None:
        await self._stop.wait()

    async def _quiescent(self) -> bool:
        assert self._publisher_pool is not None and self._runner is not None
        if any(
            consumer.job_counter or any(not task.done() for task in consumer.tasks.values())
            for consumer in self.consumers
        ):
            return False
        if self._runner.tasks:
            return False
        if any(
            await asyncio.gather(
                *(self._publisher_pool.zcard(queue) for queue in self.container.settings.worker_queues)
            )
        ):
            return False
        async with self.container.database.sessions() as session:
            if await self.container.runs.has_active_work(session):
                return False
            if await self.container.knowledge.has_active_work(session):
                return False
            if await self.container.chat_context.memory.pending(session):
                return False
            if await self.container.knowledge_jobs.generations.pending(session):
                return False
            if await self.container.document_parsing.store.active(session):
                return False
            if await self.container.graph_store.has_active_work(session):
                return False
        return True

    async def _drain(self) -> None:
        stable, last_maintenance = 0, 0
        while True:
            await asyncio.sleep(0.1)
            if not self._dispatch_passes or self._maintenance_passes == last_maintenance:
                continue
            last_maintenance = self._maintenance_passes
            stable = stable + 1 if await self._quiescent() else 0
            if stable >= QUIET_PASSES:
                return

    def _install_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(signum)
            previous_handle = getattr(loop, '_signal_handlers', {}).get(signum)
            try:
                loop.add_signal_handler(signum, self._stop.set)
            except (NotImplementedError, RuntimeError, ValueError):
                continue
            self._signals.append((signum, previous, previous_handle))

    def _restore_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for signum, previous, previous_handle in self._signals:
            loop.remove_signal_handler(signum)
            if isinstance(previous_handle, asyncio.Handle):
                # asyncio has no public getter for a pre-existing loop signal callback.
                loop.add_signal_handler(signum, previous_handle._run)
            else:
                signal.signal(signum, cast('Callable[[int, FrameType | None], None] | signal.Handlers', previous))
        self._signals.clear()

    async def close(self) -> None:
        self._stop.set()
        if not self._closing:
            self._closing = True
            if (
                self._run_task is not None
                and self._run_task is not asyncio.current_task()
                and not self._run_task.done()
            ):
                self._run_task.cancel()
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown(), name='worker-cleanup')
        cancelled = False
        while not self._shutdown_task.done():
            try:
                await asyncio.shield(self._shutdown_task)
            except asyncio.CancelledError:  # noqa: PERF203 - cleanup must survive repeated cancellation
                cancelled = True
        self._shutdown_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _shutdown(self) -> None:
        errors: list[BaseException] = []
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        for consumer in self.consumers:
            await self._close_consumer(consumer, errors)
        if self._runner is not None:
            try:
                await self._runner.close()
            except BaseException as error:
                errors.append(error)
        try:
            await self.container.redis.delete(self.container.settings.worker_health_key)
        except BaseException as error:
            errors.append(error)
        if self._publisher_pool is not None:
            try:
                await self._publisher_pool.close(close_connection_pool=True)
            except BaseException as error:
                errors.append(error)
            finally:
                self._publisher_pool = None
        self._restore_signals()
        if errors:
            for cleanup_error in errors:
                logger.error(
                    'Worker cleanup failed error_type=%s sqlstate=%s frames=%s',
                    type(cleanup_error).__name__,
                    database_sqlstate(cleanup_error) if isinstance(cleanup_error, Exception) else None,
                    exception_frames(cleanup_error) if isinstance(cleanup_error, Exception) else (),
                )
            raise errors[0]

    @staticmethod
    async def _close_consumer(consumer: Worker, errors: list[BaseException]) -> None:
        try:
            await consumer.close()
        except BaseException as error:
            errors.append(error)
        finally:
            # A failing ARQ gather must not leave sibling cancellation/SQL cleanup running.
            await asyncio.gather(*consumer.tasks.values(), return_exceptions=True)
            if isinstance(consumer, ConsumerWorker):
                errors.extend(await consumer.wait_for_finalizers())
            # ARQ skips its pool close when gathering jobs/deleting health fails.
            if consumer._pool is not None:
                try:
                    await consumer._pool.close(close_connection_pool=True)
                except BaseException as error:
                    errors.append(error)
                finally:
                    consumer._pool = None
