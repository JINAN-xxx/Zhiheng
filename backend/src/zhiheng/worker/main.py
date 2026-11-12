import asyncio
from typing import TYPE_CHECKING, cast
from uuid import UUID

from arq.connections import RedisSettings

from zhiheng.bootstrap.container import Container
from zhiheng.bootstrap.settings import Settings
from zhiheng.worker.consumer import ConsumerWorker
from zhiheng.worker.process import WorkerProcess

if TYPE_CHECKING:
    from arq.typing import WorkerCoroutine


async def execute_chat(context: dict[str, object], run_id: str) -> None:
    await cast('Container', context['container']).run_executor.execute(UUID(run_id))


async def execute_knowledge(context: dict[str, object], job_id: str, kind: str) -> None:
    container = cast('Container', context['container'])
    if kind == 'build':
        await container.knowledge_jobs.tick(UUID(job_id))
    elif kind == 'gc':
        await container.knowledge_jobs.gc(UUID(job_id))
    else:
        await container.knowledge_preparation.tick(UUID(job_id))


def build_worker(container: Container, *, burst: bool = False, max_jobs: int = 10) -> WorkerProcess:
    consumers = tuple(
        ConsumerWorker(
            functions=[cast('WorkerCoroutine', execute_chat), cast('WorkerCoroutine', execute_knowledge)],
            redis_settings=RedisSettings.from_dsn(container.settings.redis_url.get_secret_value()),
            queue_name=queue_name,
            ctx={'container': container},
            keep_result=0,
            max_jobs=limit,
            max_tries=1,
            retry_jobs=False,
            job_timeout=container.settings.provider_timeout_seconds + 10,
            health_check_interval=5,
            health_check_key=f'{queue_name}:arq-health',
            handle_signals=False,
            burst=False,
            poll_delay=0.05,
        )
        for queue_name, limit in zip(container.settings.worker_queues, (max_jobs, 4, 2, 4), strict=True)
    )
    return WorkerProcess(container, consumers, burst=burst)


async def run_worker(settings: Settings) -> None:
    container = Container(settings)
    worker: WorkerProcess | None = None
    try:
        worker = build_worker(container)
        await worker.async_run()
    finally:
        cleanup = asyncio.create_task(_close_resources(container, settings, worker), name='worker-container-cleanup')
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:  # noqa: PERF203 - container cleanup also survives repeated cancellation
                cancelled = True
        cleanup.result()
        if cancelled:
            raise asyncio.CancelledError


async def _close_resources(container: Container, settings: Settings, worker: WorkerProcess | None) -> None:
    try:
        if worker is not None:
            await worker.close()
    finally:
        try:
            await container.redis.delete(settings.worker_health_key)
        finally:
            await container.close()
