"""Base deletion records graph retirement and cancellation in its existing transaction."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.knowledge.graph.contracts import GRAPH_TERMINAL
from zhiheng.modules.knowledge.graph.orm import GraphAttempt, GraphConfiguration, GraphGeneration, GraphJob, GraphPin
from zhiheng.shared.time import utcnow


async def retire_base_graph(session: AsyncSession, base_id: UUID) -> None:
    """Caller already owns the knowledge-base row lock; no external IO occurs."""
    config = await session.scalar(
        select(GraphConfiguration).where(GraphConfiguration.base_id == base_id).with_for_update()
    )
    if config is None:
        return
    now = utcnow()
    if config.active_revision is not None:
        active = await session.get(GraphGeneration, config.active_revision)
        if active is not None and active.status == 'active':
            active.status = 'retired'
            active.gc_after = now + timedelta(seconds=60)
        config.active_revision = None
    jobs = list(
        await session.scalars(
            select(GraphJob)
            .where(GraphJob.base_id == base_id, GraphJob.status.not_in(GRAPH_TERMINAL))
            .order_by(GraphJob.id)
            .with_for_update()
        )
    )
    for job in jobs:
        job.cancel_requested_at = now
        job.updated_at = now
        job.version += 1
        attempt = await session.get(GraphAttempt, job.attempt_id)
        if attempt is not None and attempt.owner_token is None:
            job.status = 'cancelled'
            job.phase = 'finished'
            job.finished_at = now
            job.error = {'code': 'graph_cancelled', 'message': '知识库删除，图谱作业已取消'}
            attempt.status = 'cancelled'
            attempt.writer_stopped = True
            attempt.stopped_at = now
            await session.execute(delete(GraphPin).where(GraphPin.job_id == job.id))
            if job.operation != 'query' and job.graph_revision is not None:
                generation = await session.get(GraphGeneration, job.graph_revision)
                if generation is not None:
                    generation.status = 'retired'
                    generation.gc_after = now
        else:
            job.status = 'draining'
