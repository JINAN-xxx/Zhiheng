"""Fenced knowledge build facts; model and parser IO belongs to application."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Uuid, delete, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.files.public import SourceSnapshot
from zhiheng.modules.knowledge.adapters.milvus import VectorSpec
from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl
from zhiheng.modules.knowledge.chunks import TextChunk
from zhiheng.modules.knowledge.contracts import JOB_TERMINAL
from zhiheng.modules.knowledge.orm import (
    IndexGeneration,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeJob,
    KnowledgeStep,
)
from zhiheng.modules.knowledge.service import KnowledgeService, binding_fingerprint
from zhiheng.modules.models.public import ModelBinding
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

GC_DRAIN_GRACE_SECONDS = 75


@dataclass(frozen=True)
class BuildClaim:
    id: UUID
    token: UUID
    attempt_id: UUID
    revision: UUID
    base_id: UUID
    owner_id: UUID
    actor_id: UUID
    phase: str
    operation: str
    binding: ModelBinding
    documents: list[dict[str, object]]
    started_at: datetime
    lease_until: datetime
    io_control: VectorIOControl | None = None


@dataclass(frozen=True)
class EmbeddingBatch:
    document_id: UUID
    key: str
    ids: tuple[UUID, ...]
    texts: tuple[str, ...]
    dimension: int | None


class BuildStore:
    def __init__(self, knowledge: KnowledgeService) -> None:
        self.knowledge = knowledge

    async def model_guard(
        self, session: AsyncSession, job_id: UUID, run_id: UUID
    ) -> tuple[UUID, ModelBinding, UUID, str]:
        job = await session.get(KnowledgeJob, job_id)
        if job is None or job.status in JOB_TERMINAL or job.status == 'cancel_requested':
            raise AppError('knowledge_execution_cancelled', '知识模型步骤已失效', 409)
        base = await session.get(KnowledgeBase, job.base_id)
        generation = await session.get(IndexGeneration, job.index_revision)
        step = await session.scalar(
            select(KnowledgeStep).where(KnowledgeStep.parent_id == job_id, KnowledgeStep.run_id == run_id)
        )
        if (
            base is None
            or base.deleted_at is not None
            or base.active_job_id != job_id
            or generation is None
            or step is None
        ):
            raise AppError('knowledge_execution_cancelled', '知识模型步骤已失效', 409)
        document = await self.knowledge.document(session, base.id, UUID(step.step_key.split(':')[1]))
        return base.owner_id, ModelBinding.model_validate(generation.binding), document.file_id, document.source_sha256

    async def children(self, session: AsyncSession, parent_id: UUID) -> list[UUID]:
        return list(await session.scalars(select(KnowledgeStep.run_id).where(KnowledgeStep.parent_id == parent_id)))

    async def interrupted_children(self, session: AsyncSession, job_id: UUID) -> tuple[UUID, list[UUID]] | None:
        job = await session.get(KnowledgeJob, job_id)
        if job is None or job.status not in {'cancelled', 'failed', 'interrupted'}:
            return None
        base = await session.get(KnowledgeBase, job.base_id)
        assert base is not None
        return base.owner_id, await self.children(session, job_id)

    async def pending(self, session: AsyncSession) -> list[tuple[UUID, str]]:
        return [
            (row.id, str(row.version))
            for row in await session.scalars(
                select(KnowledgeJob).where(KnowledgeJob.status.not_in(JOB_TERMINAL)).limit(100)
            )
        ]

    async def claim(self, session: AsyncSession, job_id: UUID) -> BuildClaim | None:
        job = await session.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
        if job is None or job.status in JOB_TERMINAL:
            return None
        if job.lease_until is not None:
            if job.lease_until > utcnow():
                return None
            await self.fail(session, job, 'interrupted', 'knowledge_worker_lost', '知识执行进程失联')
            return None
        base = await session.get(KnowledgeBase, job.base_id)
        generation = await session.get(IndexGeneration, job.index_revision)
        assert base is not None and generation is not None
        if job.status == 'cancel_requested':
            await self.fail(session, job, 'cancelled', 'knowledge_cancelled', '知识任务已取消')
            return None
        started = utcnow()
        job.tick_token, job.lease_until = uuid4(), started + timedelta(seconds=30)
        job.status, job.updated_at = 'running', started
        return BuildClaim(
            job.id,
            job.tick_token,
            job.attempt_id,
            job.index_revision,
            base.id,
            base.owner_id,
            job.actor_id,
            job.phase,
            job.operation,
            ModelBinding.model_validate(generation.binding),
            list(job.documents),
            started,
            job.lease_until,
        )

    async def owned(self, session: AsyncSession, claim: BuildClaim) -> KnowledgeJob:
        job = await session.scalar(select(KnowledgeJob).where(KnowledgeJob.id == claim.id).with_for_update())
        if (
            job is None
            or job.attempt_id != claim.attempt_id
            or job.tick_token != claim.token
            or job.status not in {'running', 'waiting_model', 'publishing', 'cancel_requested'}
            or job.lease_until is None
            or job.lease_until <= utcnow()
        ):
            raise AppError('execution_lost', '知识执行权失效', 409)
        if job.status == 'cancel_requested':
            raise AppError('knowledge_cancelled', '知识任务已取消', 409)
        return job

    async def control(self, session: AsyncSession, claim: BuildClaim) -> tuple[str, datetime | None]:
        try:
            job = await self.owned(session, claim)
        except AppError as error:
            return ('cancel' if error.code == 'knowledge_cancelled' else 'lost'), None
        job.lease_until = utcnow() + timedelta(seconds=30)
        return 'active', job.lease_until

    async def release(self, session: AsyncSession, claim: BuildClaim, phase: str, status: str = 'pending') -> None:
        job = await self.owned(session, claim)
        job.phase, job.status, job.lease_until, job.tick_token = phase, status, None, None
        job.version += 1
        job.updated_at = utcnow()

    async def fail(self, session: AsyncSession, job: KnowledgeJob, status: str, code: str, message: str) -> None:
        job.status, job.error = status, {'code': code, 'message': message}
        job.lease_until, job.tick_token, job.finished_at = None, None, utcnow()
        job.version += 1
        base = await session.get(KnowledgeBase, job.base_id)
        if base is not None and base.active_job_id == job.id:
            base.active_job_id, base.last_error = None, job.error
        generation = await session.get(IndexGeneration, job.index_revision)
        if generation is not None and generation.status == 'candidate':
            generation.status = 'retired'
            generation.gc_after = utcnow() + timedelta(seconds=GC_DRAIN_GRACE_SECONDS)
        for entry in job.documents:
            document = await session.get(KnowledgeDocument, UUID(str(entry['document_id'])))
            if document is not None and document.job_id == job.id:
                document.status = 'deleting' if job.operation.startswith('delete') else 'failed'
                document.error = job.error

    async def fail_claim(self, session: AsyncSession, claim: BuildClaim, status: str, code: str, message: str) -> None:
        job = await session.scalar(select(KnowledgeJob).where(KnowledgeJob.id == claim.id).with_for_update())
        if (
            job
            and job.tick_token == claim.token
            and job.attempt_id == claim.attempt_id
            and job.status not in JOB_TERMINAL
        ):
            await self.fail(session, job, 'cancelled' if job.status == 'cancel_requested' else status, code, message)

    async def source(self, session: AsyncSession, claim: BuildClaim, document_id: UUID) -> tuple[UUID, int, int]:
        await self.owned(session, claim)
        row = await self.knowledge.document(session, claim.base_id, document_id)
        return row.file_id, row.chunk_size, row.chunk_overlap

    async def parsed(
        self,
        session: AsyncSession,
        claim: BuildClaim,
        document_id: UUID,
        chunks: list[TextChunk],
        source: SourceSnapshot,
    ) -> None:
        job = await self.owned(session, claim)
        row = await self.knowledge.document(session, claim.base_id, document_id)
        principal = await self.knowledge.identity.principal_for_user(session, claim.owner_id)
        if source.sha256 != row.source_sha256:
            raise AppError('source_snapshot_changed', '知识来源已变化', 409)
        await self.knowledge.files.bind_sources(
            session, principal, (source,), resource_type='knowledge_document', resource_id=row.id
        )
        row.artifact_file_id = source.artifact_file_id
        session.add_all(
            [
                KnowledgeChunk(
                    generation_id=claim.revision,
                    id=uuid4(),
                    document_id=document_id,
                    document_revision=claim.revision,
                    file_id=row.file_id,
                    ordinal=ordinal,
                    content=chunk.content,
                    content_hash=chunk.content_hash,
                    model_text=chunk.model_text,
                    vector=None,
                )
                for ordinal, chunk in enumerate(chunks)
            ]
        )
        job.documents = [
            {**entry, 'status': 'parsed', 'chunk_count': len(chunks)}
            if entry['document_id'] == str(document_id)
            else entry
            for entry in job.documents
        ]

    async def document_failed(
        self, session: AsyncSession, claim: BuildClaim, document_id: UUID, error: AppError
    ) -> None:
        job = await self.owned(session, claim)
        row = await self.knowledge.document(session, claim.base_id, document_id)
        row.status, row.error = 'failed', {'code': error.code, 'message': error.message}
        await session.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.generation_id == claim.revision, KnowledgeChunk.document_id == document_id
            )
        )
        job.documents = [
            {**entry, 'status': 'failed', 'error': row.error} if entry['document_id'] == str(document_id) else entry
            for entry in job.documents
        ]

    async def next_batch(self, session: AsyncSession, claim: BuildClaim) -> EmbeddingBatch | None:
        job = await self.owned(session, claim)
        generation = await session.get(IndexGeneration, claim.revision)
        assert generation is not None
        for entry in job.documents:
            if entry['status'] != 'parsed':
                continue
            document_id = UUID(str(entry['document_id']))
            rows = list(
                await session.scalars(
                    select(KnowledgeChunk)
                    .where(
                        KnowledgeChunk.generation_id == claim.revision,
                        KnowledgeChunk.document_id == document_id,
                        KnowledgeChunk.vector.is_(None),
                    )
                    .order_by(KnowledgeChunk.ordinal)
                    .limit(8)
                )
            )
            if rows:
                return EmbeddingBatch(
                    document_id,
                    f'embedding:{document_id}:{rows[0].ordinal}',
                    tuple(row.id for row in rows),
                    tuple(row.model_text for row in rows),
                    generation.dimension,
                )
            job.documents = [
                {**item, 'status': 'ready'} if item['document_id'] == str(document_id) else item
                for item in job.documents
            ]
        return None

    async def vectors(
        self, session: AsyncSession, claim: BuildClaim, batch: EmbeddingBatch, vectors: list[list[float]]
    ) -> None:
        await self.owned(session, claim)
        generation = await session.get(IndexGeneration, claim.revision)
        assert generation is not None
        if generation.dimension is None:
            generation.dimension = len(vectors[0])
        for chunk_id, vector in zip(batch.ids, vectors, strict=True):
            row = await session.get(KnowledgeChunk, (claim.revision, chunk_id))
            assert row is not None
            row.vector = vector

    async def step(self, session: AsyncSession, parent_id: UUID, key: str) -> UUID | None:
        return cast(
            'UUID | None',
            await session.scalar(
                select(KnowledgeStep.run_id).where(KnowledgeStep.parent_id == parent_id, KnowledgeStep.step_key == key)
            ),
        )

    async def add_step(self, session: AsyncSession, parent_id: UUID, key: str, run_id: UUID, value: str) -> None:
        session.add(KnowledgeStep(parent_id=parent_id, step_key=key, run_id=run_id, fingerprint=value))

    async def candidate(self, session: AsyncSession, claim: BuildClaim) -> tuple[VectorSpec, list[dict[str, object]]]:
        job = await self.owned(session, claim)
        if job.documents and all(entry['status'] == 'failed' for entry in job.documents):
            raise AppError('knowledge_documents_failed', '本批文档均未完成索引，原活动版本保留', 422)
        if claim.operation == 'rebuild' and any(entry['status'] == 'failed' for entry in job.documents):
            raise AppError('knowledge_rebuild_failed', '全库重建未全部成功，原活动版本保留', 422)
        base = await session.get(KnowledgeBase, claim.base_id)
        generation = await session.get(IndexGeneration, claim.revision)
        assert base is not None and generation is not None
        old = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        if old and old.fingerprint != generation.fingerprint and claim.operation != 'rebuild':
            raise AppError('knowledge_reindex_required', 'Embedding绑定已变化，请显式重建', 409)
        if (
            old
            and claim.operation != 'rebuild'
            and old.fingerprint == generation.fingerprint
            and generation.dimension is not None
            and old.dimension != generation.dimension
        ):
            raise AppError('invalid_embedding_dimension', 'Embedding向量维度已变化，请显式重建', 409)
        if old and claim.operation != 'rebuild':
            replaced = {
                UUID(str(entry['document_id']))
                for entry in job.documents
                if entry['status'] == 'ready' or claim.operation.startswith('delete')
            }
            columns = list(KnowledgeChunk.__table__.columns)
            values = [
                literal(generation.id, type_=Uuid) if column.name == 'generation_id' else column for column in columns
            ]
            carry = (
                select(*values)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(
                    KnowledgeChunk.generation_id == old.id,
                    KnowledgeChunk.document_id.not_in(replaced),
                    KnowledgeDocument.deleted_at.is_(None),
                )
            )
            await session.execute(
                insert(KnowledgeChunk)
                .from_select([column.name for column in columns], carry)
                .on_conflict_do_nothing(index_elements=['generation_id', 'id'])
            )
            generation.dimension = generation.dimension or old.dimension
        await session.flush()
        rows = list(await session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.generation_id == generation.id)))
        if any(row.vector is None for row in rows):
            raise AppError('incomplete_index', '索引向量尚未完成', 503)
        generation.row_count = len(rows)
        generation.document_versions = {str(row.document_id): str(row.document_revision) for row in rows}
        spec = VectorSpec(
            base.id,
            generation.id,
            generation.attempt_id,
            generation.collection_name,
            generation.fingerprint,
            generation.dimension,
        )
        return spec, [
            {
                'id': str(row.id),
                'document_id': str(row.document_id),
                'content_hash': row.content_hash,
                'vector': row.vector,
            }
            for row in rows
        ]

    async def publish(self, session: AsyncSession, claim: BuildClaim, binding: ModelBinding) -> None:
        job = await self.owned(session, claim)
        base = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.id == claim.base_id).with_for_update())
        generation = await session.get(IndexGeneration, claim.revision)
        assert base is not None and generation is not None
        if (
            base.active_job_id != job.id
            or binding_fingerprint(binding) != generation.fingerprint
            or generation.status != 'candidate'
        ):
            raise AppError('execution_lost', '知识发布依赖已变化', 409)
        old = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        if old:
            old.status = 'retired'
            old.gc_after = utcnow() + timedelta(seconds=GC_DRAIN_GRACE_SECONDS)
        generation.status = 'active'
        base.active_revision, base.active_job_id, base.last_error = generation.id, None, None
        base.version, base.updated_at = base.version + 1, utcnow()
        failed = any(entry['status'] == 'failed' for entry in job.documents)
        succeeded = any(entry['status'] == 'ready' for entry in job.documents)
        job.status = 'partial' if failed and succeeded else 'failed' if failed else 'completed'
        job.phase, job.finished_at, job.lease_until, job.tick_token = 'completed', utcnow(), None, None
        job.version += 1
        for entry in job.documents:
            document = await session.get(KnowledgeDocument, UUID(str(entry['document_id'])))
            assert document is not None
            if entry['status'] == 'ready':
                document.status, document.active_revision, document.error = 'ready', generation.id, None
                document.chunk_count = int(str(entry['chunk_count']))
                if claim.operation.startswith('delete'):
                    document.status, document.active_revision = 'deleted', None
                    await self.knowledge.files.release_resources(
                        session,
                        resource_type='knowledge_document',
                        resource_ids=(document.id,),
                        actor_id=claim.actor_id,
                    )
            document.updated_at = utcnow()
        if claim.operation == 'delete_base':
            base.active_revision, generation.status = None, 'retired'
            generation.gc_after = utcnow() + timedelta(seconds=GC_DRAIN_GRACE_SECONDS)
