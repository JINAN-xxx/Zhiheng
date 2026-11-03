import asyncio
import signal
from time import time
from typing import TYPE_CHECKING, cast
from uuid import UUID

from arq import Worker, cron
from arq.connections import ArqRedis, RedisSettings

from zhiheng.application.lifecycle import RunLifecycle
from zhiheng.bootstrap.container import Container
from zhiheng.bootstrap.settings import Settings
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.worker.execution import execute_run
from zhiheng.worker.graph_runner import GraphRunner

if TYPE_CHECKING:
    from arq.typing import StartupShutdown, WorkerCoroutine


async def execute_chat(context: dict[str, object], run_id: str) -> None:
    await execute_run(cast('Container', context['container']), UUID(run_id))
    await publish_knowledge(cast('Container', context['container']), cast('ArqRedis', context['redis']))


async def execute_knowledge(context: dict[str, object], job_id: str, kind: str) -> None:
    container = cast('Container', context['container'])
    if kind == 'build':
        await container.knowledge_jobs.tick(UUID(job_id))
    elif kind == 'gc':
        await container.knowledge_jobs.gc(UUID(job_id))
    else:
        await container.knowledge_preparation.tick(UUID(job_id))
    await publish_outbox(container, cast('ArqRedis', context['redis']))
    await publish_knowledge(container, cast('ArqRedis', context['redis']))


async def publish_knowledge(container: Container, queue: ArqRedis) -> None:
    async with container.database.sessions() as session:
        builds = await container.knowledge_jobs.store.pending(session)
        preparations = await container.knowledge_preparation.store.pending(session)
        generations = await container.knowledge_jobs.generations.pending(session)
    for kind, jobs in [('build', builds), ('retrieval', preparations)]:
        for job_id, version in jobs:
            await queue.enqueue_job(
                'execute_knowledge',
                str(job_id),
                kind,
                _job_id=f'knowledge:{kind}:{job_id}:{version}',
                _queue_name=container.settings.worker_queue,
            )
    for generation_id in generations:
        await queue.enqueue_job(
            'execute_knowledge',
            str(generation_id),
            'gc',
            _job_id=f'knowledge:gc:{generation_id}',
            _queue_name=container.settings.worker_queue,
        )


RECOVERY_LIMIT = 100


async def recover_expired(container: Container) -> None:
    # Release a run's Key/account locks before acquiring another run's Key.
    # The interrupted state and its usage outcome still commit atomically.
    for _ in range(RECOVERY_LIMIT):
        async with container.database.sessions.begin() as session:
            recovered = await container.runs.recover_one(session)
            if recovered is None:
                return
            run_id, provider_started = recovered
            await container.usage.settle(session, run_id, rejected_before_execution=not provider_started)
            await RunLifecycle(container.chat, container.work).project(
                session, await container.runs.projection(session, run_id)
            )


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
    async with container.database.sessions.begin() as session:
        await container.skills.cleanup_expired(session)
    await recover_expired(container)
    await container.chat_context.reconcile()
    await publish_outbox(container, queue)
    await publish_knowledge(container, queue)
    runner = context.get('graph_runner')
    if isinstance(runner, GraphRunner):
        runner.require_running()
    await container.redis.set(container.settings.worker_health_key, str(time()), ex=15)


async def file_maintenance(context: dict[str, object]) -> None:
    container = cast('Container', context['container'])
    system = Principal(UUID(int=0), 'files-maintenance', True)
    try:
        for kind in ('reminders', 'expiry', 'purge', 'reconcile'):
            await container.file_jobs.run(system, kind)
    except AppError as error:
        if error.code != 'file_job_busy':
            raise


async def worker_startup(context: dict[str, object]) -> None:
    container = cast('Container', context['container'])
    await container.database.require_schema()

    async def publish_graph_children() -> None:
        await publish_outbox(container, cast('ArqRedis', context['redis']))

    runner = GraphRunner(container.graph_jobs, container.settings.graph_runner_limit, publish_graph_children)
    context['graph_runner'] = runner
    await runner.start()

    def supervise_graph(task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            worker = context.get('worker')
            if isinstance(worker, Worker):
                worker.handle_sig(signal.SIGTERM)

    if runner.poller is not None:
        runner.poller.add_done_callback(supervise_graph)
    await periodic(context)


async def worker_shutdown(context: dict[str, object]) -> None:
    runner = context.get('graph_runner')
    if isinstance(runner, GraphRunner):
        await runner.close()


def build_worker(container: Container, *, burst: bool = False, max_jobs: int = 10) -> Worker:
    worker = Worker(
        functions=[cast('WorkerCoroutine', execute_chat), cast('WorkerCoroutine', execute_knowledge)],
        cron_jobs=[]
        if burst
        else [
            cron(
                cast('WorkerCoroutine', file_maintenance), minute={0, 15, 30, 45}, second=20, keep_result=0, timeout=180
            ),
            cron(
                cast('WorkerCoroutine', periodic), second=set(range(60)), run_at_startup=True, keep_result=0, timeout=10
            ),
        ],
        redis_settings=RedisSettings.from_dsn(container.settings.redis_url.get_secret_value()),
        queue_name=container.settings.worker_queue,
        ctx={'container': container},
        on_startup=cast('StartupShutdown', worker_startup),
        on_shutdown=cast('StartupShutdown', worker_shutdown),
        keep_result=0,
        max_jobs=max_jobs,
        max_tries=1,
        retry_jobs=False,
        job_timeout=container.settings.provider_timeout_seconds + 10,
        health_check_interval=5,
        health_check_key=f'{container.settings.worker_queue}:arq-health',
        handle_signals=not burst,
        burst=burst,
    )
    worker.ctx['worker'] = worker
    return worker


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
