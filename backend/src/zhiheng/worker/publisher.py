"""Transport durable deliveries without taking an execution slot."""

from arq.connections import ArqRedis

from zhiheng.application.execution.dispatch import MODEL_TYPE_PATH, execution_lane
from zhiheng.bootstrap.container import Container


async def publish_outbox(container: Container, queue: ArqRedis) -> None:
    async with container.database.sessions() as session:
        pending = await container.outbox.pending(session, payload_path=MODEL_TYPE_PATH)
    for delivery in pending:
        queue_name = (
            container.settings.worker_short_queue
            if execution_lane(delivery.hint) == 'short'
            else container.settings.worker_queue
        )
        await queue.enqueue_job('execute_chat', str(delivery.run_id), _job_id=str(delivery.id), _queue_name=queue_name)
        async with container.database.sessions.begin() as session:
            await container.outbox.mark_sent(session, delivery.id)


async def publish_knowledge(container: Container, queue: ArqRedis) -> None:
    async with container.database.sessions() as session:
        builds = await container.knowledge_jobs.store.pending(session)
        preparations = await container.knowledge_preparation.store.pending(session)
        generations = await container.knowledge_jobs.generations.pending(session)
    for kind, jobs, queue_name in (
        ('build', builds, container.settings.worker_knowledge_queue),
        ('retrieval', preparations, container.settings.worker_retrieval_queue),
    ):
        for job_id, version in jobs:
            await queue.enqueue_job(
                'execute_knowledge',
                str(job_id),
                kind,
                _job_id=f'knowledge:{kind}:{job_id}:{version}',
                _queue_name=queue_name,
            )
    for generation_id in generations:
        await queue.enqueue_job(
            'execute_knowledge',
            str(generation_id),
            'gc',
            _job_id=f'knowledge:gc:{generation_id}',
            _queue_name=container.settings.worker_knowledge_queue,
        )
