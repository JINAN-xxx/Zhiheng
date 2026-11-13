"""A real, independently killable single-slot worker for recovery tests."""

import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from arq.connections import ArqRedis
from arq.jobs import Job
from sqlalchemy import event

from tests.core.resources import resource
from zhiheng.bootstrap.container import Container
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC
from zhiheng.runtime.orm import Outbox
from zhiheng.worker.main import build_worker


def register_job(job_id: str) -> None:
    for prefix in ('arq:job:', 'arq:result:', 'arq:in-progress:', 'arq:retry:'):
        resource('intent', 'redis_key', prefix + job_id)


def outbox_intent(mapper: object, connection: object, target: Outbox) -> None:
    if target.id is None:
        target.id = uuid4()
    register_job(str(target.id))


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger('arq').setLevel(logging.INFO)
    event.listen(Outbox, 'before_insert', outbox_intent)
    enqueue = ArqRedis.enqueue_job

    async def registered(queue: ArqRedis, *args: object, **kwargs: object) -> Job | None:
        job_id = kwargs.get('_job_id')
        if isinstance(job_id, str):
            register_job(job_id)
        return await enqueue(queue, *args, **kwargs)

    ArqRedis.enqueue_job = registered
    invoke = GraphVectorRPC._invoke

    def registered_rpc(owner: GraphVectorRPC, name: str, args: tuple[object, ...], kwargs: dict[str, Any]) -> Any:
        if name == 'create_collection':
            resource(
                'intent',
                'milvus_collection',
                str(args[0]),
                description=kwargs['schema'].description,
                database='default',
            )
        return invoke(owner, name, args, kwargs)

    GraphVectorRPC._invoke = registered_rpc
    settings = Settings.model_validate(json.loads(Path(sys.argv[1]).read_text()))  # noqa: ASYNC240 - load the one private local config synchronously before worker startup
    for key in (*settings.worker_queues, settings.worker_health_key, *settings.worker_arq_health_keys):
        resource('intent', 'redis_key', key)
    container = Container(settings)
    worker = build_worker(container, max_jobs=1)
    try:
        await worker.async_run()
    finally:
        await worker.close()
        await container.close()


if __name__ == '__main__':
    asyncio.run(main())
