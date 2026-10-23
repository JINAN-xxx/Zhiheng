from time import time
from typing import TYPE_CHECKING, cast
from uuid import UUID

from arq import Worker, cron
from arq.connections import ArqRedis, RedisSettings

from zhiheng.bootstrap.container import Container
from zhiheng.bootstrap.settings import Settings
from zhiheng.worker.execution import execute_run

if TYPE_CHECKING:
    from arq.typing import StartupShutdown, WorkerCoroutine


async def execute_chat(context: dict[str, object], run_id: str) -> None:
    await execute_run(cast('Container', context['container']), UUID(run_id))


async def recover_expired(container: Container) -> None:
    async with container.database.sessions.begin() as session:
        recovered = await container.runs.recover(session)
        for run_id, provider_started in recovered:
            await container.usage.settle(session, run_id, rejected_before_execution=not provider_started)


async def publish_outbox(container: Container, queue: ArqRedis) -> None:
    async with container.database.sessions() as session:
        pending = await container.outbox.pending(session)
    for outbox_id, run_id in pending:
        await queue.enqueue_job(
            'execute_chat',
            str(run_id),
            _job_id=str(outbox_id),
            _queue_name=container.settings.worker_queue,
        )
        async with container.database.sessions.begin() as session:
            await container.outbox.mark_sent(session, outbox_id)


async def periodic(context: dict[str, object]) -> None:
    container, queue = cast('Container', context['container']), cast('ArqRedis', context['redis'])
    await recover_expired(container)
    await publish_outbox(container, queue)
    await container.redis.set(container.settings.worker_health_key, str(time()), ex=15)


async def worker_startup(context: dict[str, object]) -> None:
    container = cast('Container', context['container'])
    await container.database.require_schema()
    await periodic(context)


def build_worker(container: Container, *, burst: bool = False) -> Worker:
    return Worker(
        functions=[cast('WorkerCoroutine', execute_chat)],
        cron_jobs=[]
        if burst
        else [
            cron(
                cast('WorkerCoroutine', periodic), second=set(range(60)), run_at_startup=True, keep_result=0, timeout=10
            )
        ],
        redis_settings=RedisSettings.from_dsn(container.settings.redis_url.get_secret_value()),
        queue_name=container.settings.worker_queue,
        ctx={'container': container},
        on_startup=cast('StartupShutdown', worker_startup),
        keep_result=0,
        max_tries=1,
        retry_jobs=False,
        job_timeout=container.settings.provider_timeout_seconds + 10,
        health_check_interval=5,
        health_check_key=f'{container.settings.worker_queue}:arq-health',
        handle_signals=not burst,
        burst=burst,
    )


async def run_worker(settings: Settings) -> None:
    container = Container(settings)
    worker: Worker | None = None
    try:
        worker = build_worker(container)
        await worker.async_run()
    finally:
        try:
            if worker is not None:
                await worker.close()
        finally:
            try:
                await container.redis.delete(settings.worker_health_key)
            finally:
                await container.close()
