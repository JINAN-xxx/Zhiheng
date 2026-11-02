"""One current epoch owns every callback, publish and writer-stopped transition."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.knowledge.graph.contracts import (
    GRAPH_TERMINAL,
    GraphFrozen,
    GraphModelOperation,
    GraphModelRole,
    GraphOperation,
)
from zhiheng.modules.knowledge.graph.orm import (
    GraphAttempt,
    GraphConfiguration,
    GraphGeneration,
    GraphJob,
    GraphPin,
    GraphStep,
)
from zhiheng.modules.knowledge.graph.service import GraphService
from zhiheng.modules.knowledge.orm import KnowledgeBase
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


@dataclass(frozen=True)
class GraphClaim:
    job_id: UUID
    base_id: UUID
    owner_id: UUID
    actor_id: UUID
    attempt_id: UUID
    token: UUID
    epoch: int
    operation: GraphOperation
    revision: UUID
    workspace: str
    deadline: datetime
    lease_until: datetime
    frozen: GraphFrozen
    request: dict[str, object]


class GraphStore:
    def __init__(self, service: GraphService) -> None:
        self.service = service

    async def pending(self, session: AsyncSession) -> list[UUID]:
        return list(
            await session.scalars(
                select(GraphJob.id).where(GraphJob.status == 'pending').order_by(GraphJob.created_at).limit(4)
            )
        )

    async def claim(self, session: AsyncSession, job_id: UUID) -> GraphClaim | None:
        job = await session.scalar(select(GraphJob).where(GraphJob.id == job_id).with_for_update())
        if job is None or job.status != 'pending':
            return None
        attempt = await session.scalar(select(GraphAttempt).where(GraphAttempt.id == job.attempt_id).with_for_update())
        if attempt is None or job.graph_revision is None:
            raise AppError('graph_attempt_invalid', '图谱作业缺少完整attempt身份', 503)
        if attempt.status != 'pending' or attempt.owner_token is not None or attempt.writer_stopped:
            raise AppError('graph_attempt_invalid', '图谱attempt已使用，必须创建新namespace重试', 409)
        if attempt.deadline <= utcnow():
            attempt.status = 'interrupted'
            attempt.writer_stopped = True
            attempt.stopped_at = utcnow()
            job.status = 'interrupted'
            job.phase = 'finished'
            job.finished_at = utcnow()
            job.updated_at = utcnow()
            job.version += 1
            job.error = {'code': 'graph_deadline_expired', 'message': '图谱作业已超过执行期限，请显式重试'}
            return None
        attempt.owner_token = uuid4()
        attempt.status = 'running'
        attempt.lease_until = utcnow() + timedelta(seconds=10)
        job.status = 'running'
        job.phase = 'initializing'
        job.processed_documents = None
        job.version += 1
        job.updated_at = utcnow()
        return GraphClaim(
            job.id,
            job.base_id,
            job.owner_id,
            job.actor_id,
            attempt.id,
            attempt.owner_token,
            attempt.epoch,
            cast('GraphOperation', job.operation),
            job.graph_revision,
            attempt.workspace,
            attempt.deadline,
            attempt.lease_until,
            GraphFrozen.model_validate(job.frozen),
            dict(job.request),
        )

    async def _owned(self, session: AsyncSession, claim: GraphClaim) -> tuple[GraphJob, GraphAttempt]:
        await self.service.lock_models(session, claim.frozen)
        owner = await self.service.knowledge.identity.principal_for_user(session, claim.owner_id)
        await self.service.knowledge.require(session, owner, claim.base_id, lock=True)
        job = await session.scalar(
            select(GraphJob)
            .where(GraphJob.id == claim.job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        attempt = await session.scalar(
            select(GraphAttempt)
            .where(GraphAttempt.id == claim.attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            job is None
            or attempt is None
            or job.attempt_id != claim.attempt_id
            or attempt.owner_token != claim.token
            or attempt.epoch != claim.epoch
            or attempt.status != 'running'
            or job.status in GRAPH_TERMINAL
            or attempt.lease_until is None
            or attempt.lease_until <= utcnow()
            or attempt.deadline <= utcnow()
        ):
            raise AppError('graph_execution_lost', '图谱执行权已失效', 409)
        if job.cancel_requested_at is not None:
            raise AppError('graph_cancelled', '图谱作业已取消', 409)
        owner = await self.service.knowledge.identity.principal_for_user(session, claim.owner_id)
        actor = await self.service.knowledge.identity.principal_for_user(session, claim.actor_id)
        await self.service.knowledge.require(
            session, actor, claim.base_id, manage=claim.operation in {'build', 'preview'}
        )
        await self.service.authorize(session, owner, claim.base_id, claim.frozen)
        if actor.id != owner.id:
            await self.service.authorize(session, actor, claim.base_id, claim.frozen)
        return job, attempt

    async def require_owned(self, session: AsyncSession, claim: GraphClaim) -> None:
        await self._owned(session, claim)

    async def renew(self, session: AsyncSession, claim: GraphClaim) -> datetime:
        _, attempt = await self._owned(session, claim)
        attempt.lease_until = utcnow() + timedelta(seconds=10)
        return attempt.lease_until

    async def next_step(self, session: AsyncSession, claim: GraphClaim) -> int:
        job, attempt = await self._owned(session, claim)
        attempt.next_ordinal += 1
        job.status = 'waiting_model'
        job.phase = 'model'
        job.updated_at = utcnow()
        job.version += 1
        return attempt.next_ordinal

    async def callback_returned(self, session: AsyncSession, claim: GraphClaim) -> None:
        job, _ = await self._owned(session, claim)
        job.status = 'running'
        job.phase = 'processing'
        job.updated_at = utcnow()
        job.version += 1

    async def guard(self, session: AsyncSession, run_id: UUID, attempt_id: UUID) -> GraphClaim:
        step = await session.scalar(
            select(GraphStep).where(GraphStep.run_id == run_id, GraphStep.attempt_id == attempt_id)
        )
        attempt = await session.get(GraphAttempt, attempt_id)
        job = await session.get(GraphJob, attempt.job_id) if attempt else None
        if (
            step is None
            or attempt is None
            or job is None
            or attempt.owner_token is None
            or job.graph_revision is None
            or attempt.lease_until is None
        ):
            raise AppError('graph_execution_lost', '图谱步骤归属无效', 409)
        claim = GraphClaim(
            job.id,
            job.base_id,
            job.owner_id,
            job.actor_id,
            attempt.id,
            attempt.owner_token,
            step.epoch,
            cast('GraphOperation', job.operation),
            job.graph_revision,
            attempt.workspace,
            attempt.deadline,
            attempt.lease_until,
            GraphFrozen.model_validate(job.frozen),
            dict(job.request),
        )
        await self._owned(session, claim)
        return claim

    async def _publish(self, session: AsyncSession, claim: GraphClaim) -> None:
        config = await session.scalar(
            select(GraphConfiguration).where(GraphConfiguration.base_id == claim.base_id).with_for_update()
        )
        if config is None or config.version != claim.frozen.config.version:
            raise AppError('graph_publish_conflict', '图谱配置已变化，候选不能发布', 409)
        generation = await session.get(GraphGeneration, claim.revision)
        if generation is None or generation.attempt_id != claim.attempt_id or generation.status != 'candidate':
            raise AppError('graph_publish_conflict', '图谱候选身份或状态不匹配', 409)
        self.service._validate_generation(generation)
        previous = await session.get(GraphGeneration, config.active_revision) if config.active_revision else None
        if previous is not None:
            previous.status = 'retired'
            previous.gc_after = utcnow() + timedelta(seconds=60)
        config.active_revision = claim.revision
        generation.status = 'active'

    async def finish(
        self,
        session: AsyncSession,
        claim: GraphClaim,
        *,
        status: str,
        result: dict[str, object] | None,
        error: dict[str, object] | None,
        stopped: bool,
    ) -> None:
        if status == 'completed':
            await self.service.lock_models(session, claim.frozen)
        await session.execute(select(KnowledgeBase.id).where(KnowledgeBase.id == claim.base_id).with_for_update())
        job = await session.scalar(select(GraphJob).where(GraphJob.id == claim.job_id).with_for_update())
        attempt = await session.scalar(
            select(GraphAttempt).where(GraphAttempt.id == claim.attempt_id).with_for_update()
        )
        if job is None or attempt is None or attempt.owner_token != claim.token:
            return
        if attempt.epoch != claim.epoch:
            if stopped and attempt.status == 'interrupted':
                # Recovery never reuses this attempt namespace or owner token.
                # The original worker may report actual drain after losing its
                # epoch; this proof grants no result or publish authority.
                attempt.writer_stopped = True
                attempt.stopped_at = utcnow()
                await session.execute(delete(GraphPin).where(GraphPin.job_id == job.id))
                generation = await session.get(GraphGeneration, claim.revision)
                if generation is not None and generation.attempt_id == claim.attempt_id:
                    generation.status = 'retired'
                    generation.gc_after = utcnow() + timedelta(seconds=60)
            return
        if status == 'completed':
            await self._owned(session, claim)
            if not stopped:
                raise AppError('graph_writer_not_stopped', '图谱写入尚未停止', 409)
            if claim.operation == 'build':
                await self._publish(session, claim)
            job.processed_documents = job.total_documents
            if claim.operation != 'query' and result is not None:
                job.progress = {
                    'extracted_chunks': result['chunks'],
                    'total_chunks': result['chunks'],
                    'entities': result['entities'],
                    'relations': result['relations'],
                }
        if job.cancel_requested_at is not None:
            status = 'cancelled'
            result = None
            error = {'code': 'graph_cancelled', 'message': '图谱作业已取消'}
        attempt.writer_stopped = stopped
        attempt.stopped_at = utcnow() if stopped else None
        attempt.status = status
        if claim.operation != 'query' and (status != 'completed' or claim.operation == 'preview'):
            generation = await session.get(GraphGeneration, claim.revision)
            if generation is not None:
                generation.status = 'retired'
                generation.gc_after = utcnow() + timedelta(seconds=60)
        await session.execute(delete(GraphPin).where(GraphPin.job_id == job.id))
        job.status = status
        job.phase = 'finished'
        job.result = result
        job.error = error
        job.finished_at = utcnow()
        job.updated_at = utcnow()
        job.version += 1

    async def save_manifest(self, session: AsyncSession, claim: GraphClaim, manifest: dict[str, object]) -> None:
        await self._owned(session, claim)
        generation = await session.get(GraphGeneration, claim.revision)
        if generation is None or generation.attempt_id != claim.attempt_id:
            raise AppError('graph_generation_missing', '图谱候选不存在', 409)
        generation.manifest = manifest

    async def manifest(self, session: AsyncSession, revision: UUID) -> dict[str, object]:
        generation = await session.get(GraphGeneration, revision)
        if generation is None:
            raise AppError('graph_generation_missing', '图谱版本不存在', 409)
        self.service._validate_generation(generation)
        return dict(generation.manifest)

    async def recover(self, session: AsyncSession) -> None:
        attempt_id = await session.scalar(
            select(GraphAttempt.id)
            .where(GraphAttempt.status == 'running', GraphAttempt.lease_until < utcnow())
            .order_by(GraphAttempt.lease_until)
            .limit(1)
        )
        if attempt_id is None:
            return
        candidate = await session.get(GraphAttempt, attempt_id)
        assert candidate is not None
        original = await session.get(GraphJob, candidate.job_id)
        if original is None:
            return
        await session.execute(select(KnowledgeBase.id).where(KnowledgeBase.id == original.base_id).with_for_update())
        job = await session.scalar(
            select(GraphJob)
            .where(GraphJob.id == original.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        attempt = await session.scalar(
            select(GraphAttempt)
            .where(GraphAttempt.id == attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            job is None
            or attempt is None
            or attempt.status != 'running'
            or attempt.lease_until is None
            or attempt.lease_until >= utcnow()
        ):
            return
        attempt.status = 'interrupted'
        attempt.epoch += 1
        # No live-process/RPC ownership proof exists after lease loss. Retain
        # writer_stopped=False and forbid collection of the old namespace.
        job.status = 'interrupted'
        job.phase = 'finished'
        job.finished_at = utcnow()
        job.updated_at = utcnow()
        job.version += 1
        job.error = {'code': 'graph_worker_lost', 'message': '图谱执行进程失联，请显式发起新构建'}

    async def gc_candidates(self, session: AsyncSession) -> list[UUID]:
        return list(
            await session.scalars(
                select(GraphGeneration.id)
                .join(GraphAttempt, GraphAttempt.id == GraphGeneration.attempt_id)
                .where(
                    GraphGeneration.status.in_(('retired', 'collecting', 'collected')),
                    GraphGeneration.gc_after <= utcnow(),
                    GraphAttempt.writer_stopped.is_(True),
                    ~exists(
                        select(GraphConfiguration.base_id).where(
                            GraphConfiguration.active_revision == GraphGeneration.id
                        )
                    ),
                    ~exists(
                        select(GraphPin.id).where(
                            GraphPin.generation_id == GraphGeneration.id, GraphPin.lease_until > utcnow()
                        )
                    ),
                )
                .order_by(GraphGeneration.gc_after, GraphGeneration.id)
                .limit(10)
            )
        )

    async def claim_gc(self, session: AsyncSession, revision: UUID) -> dict[str, object] | None:
        generation = await session.scalar(
            select(GraphGeneration).where(GraphGeneration.id == revision).with_for_update()
        )
        if (
            generation is None
            or generation.status not in {'retired', 'collecting', 'collected'}
            or generation.gc_after is None
            or generation.gc_after > utcnow()
        ):
            return None
        attempt = await session.get(GraphAttempt, generation.attempt_id)
        if attempt is None or not attempt.writer_stopped:
            return None
        if await session.scalar(
            select(GraphConfiguration.base_id).where(GraphConfiguration.active_revision == revision).limit(1)
        ):
            return None
        if await session.scalar(
            select(GraphPin.id).where(GraphPin.generation_id == revision, GraphPin.lease_until > utcnow()).limit(1)
        ):
            return None
        if generation.manifest:
            self.service._validate_generation(generation)
        generation.status = 'collecting'
        return dict(generation.manifest)

    async def finish_gc(self, session: AsyncSession, revision: UUID) -> None:
        generation = await session.scalar(
            select(GraphGeneration).where(GraphGeneration.id == revision).with_for_update()
        )
        if generation is not None and generation.status == 'collecting':
            generation.status = 'collected'
            generation.gc_after = utcnow() + timedelta(hours=1) if generation.manifest else None

    async def add_step(
        self,
        session: AsyncSession,
        claim: GraphClaim,
        *,
        role: GraphModelRole,
        operation: GraphModelOperation,
        ordinal: int,
        run_id: UUID,
        fingerprint: str,
    ) -> None:
        session.add(
            GraphStep(
                attempt_id=claim.attempt_id,
                epoch=claim.epoch,
                role=role,
                operation=operation,
                ordinal=ordinal,
                run_id=run_id,
                fingerprint=fingerprint,
            )
        )

    async def child_runs(self, session: AsyncSession, attempt_id: UUID) -> list[UUID]:
        return list(await session.scalars(select(GraphStep.run_id).where(GraphStep.attempt_id == attempt_id)))

    async def pin_read(
        self, session: AsyncSession, principal: Principal, base_id: UUID
    ) -> tuple[UUID, GraphFrozen, dict[str, object]]:
        generation = await self.service._active_generation(session, principal, base_id)
        token = uuid4()
        session.add(
            GraphPin(
                id=token, generation_id=generation.id, owner_token=token, lease_until=utcnow() + timedelta(seconds=60)
            )
        )
        return token, GraphFrozen.model_validate(generation.frozen), dict(generation.manifest)

    async def release_pin(self, session: AsyncSession, pin_id: UUID) -> None:
        await session.execute(delete(GraphPin).where(GraphPin.id == pin_id))

    async def authorize_step_read(self, session: AsyncSession, principal: Principal, attempt_id: UUID) -> None:
        attempt = await session.get(GraphAttempt, attempt_id)
        job = await session.get(GraphJob, attempt.job_id) if attempt else None
        if job is None:
            raise AppError('graph_job_not_found', '图谱作业不存在', 404)
        await self.service.job(session, principal, job.base_id, job.id)

    async def defer_gc(self, session: AsyncSession, revision: UUID) -> None:
        generation = await session.scalar(
            select(GraphGeneration).where(GraphGeneration.id == revision).with_for_update()
        )
        if generation is not None and generation.status == 'collecting':
            generation.gc_after = utcnow() + timedelta(minutes=5)
