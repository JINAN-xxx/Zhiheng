"""Atomic knowledge build admission and stable JobInfo projection; Worker leases live in build.py."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select, text

from zhiheng.modules.knowledge.contracts import (
    JOB_TERMINAL,
    BaseInfo,
    DocumentResult,
    JobInfo,
    KnowledgeSettings,
)
from zhiheng.modules.knowledge.graph.lifecycle import retire_base_graph
from zhiheng.modules.knowledge.orm import (
    IndexGeneration,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeFolder,
    KnowledgeJob,
    KnowledgeMaterial,
    KnowledgeUpload,
)
from zhiheng.modules.knowledge.values import BuildSelection, binding_fingerprint
from zhiheng.modules.models.public import ModelBinding
from zhiheng.runtime.contracts import RunError
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.knowledge.materials import KnowledgeMaterials
    from zhiheng.modules.knowledge.service import KnowledgeService
    from zhiheng.shared.principal import Principal


def job_info(job: KnowledgeJob) -> JobInfo:
    results = [DocumentResult.model_validate(row) for row in job.documents]
    return JobInfo(
        id=job.id,
        base_id=job.base_id,
        operation=job.operation,
        status=job.status,
        phase=job.phase,
        version=job.version,
        attempt_id=job.attempt_id,
        index_revision=job.index_revision,
        total_documents=len(results),
        completed_documents=sum(row.status == 'ready' for row in results),
        failed_documents=sum(row.status == 'failed' for row in results),
        chunk_count=sum(row.chunk_count or 0 for row in results)
        if results and all(row.chunk_count is not None for row in results)
        else None,
        document_results=results,
        error=RunError.model_validate(job.error) if job.error else None,
        created_at=job.created_at,
        updated_at=job.updated_at,
        finished_at=job.finished_at,
        cancel_allowed=job.status not in JOB_TERMINAL,
    )


class KnowledgeBuildAdmission:
    def __init__(self, catalog: KnowledgeService, materials: KnowledgeMaterials) -> None:
        self.catalog, self.materials = catalog, materials
        self.files, self.identity, self.models = catalog.files, catalog.identity, catalog.models

    async def deletion_binding(self, session: AsyncSession, base_id: UUID) -> ModelBinding:
        base = await session.get(KnowledgeBase, base_id)
        assert base is not None
        retained = await self.models.bindings.retained(session, base.embed_model_id)
        active = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        return ModelBinding.model_validate(active.binding) if active is not None else retained

    async def configure(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        body: KnowledgeSettings,
        binding: ModelBinding,
        value: str,
    ) -> tuple[BaseInfo, JobInfo | None]:
        base = await self.catalog.require(session, principal, base_id, manage=True, lock=True)
        self.catalog.version(base.version, body.expected_version)
        self.catalog.idle(base)
        ids = await self.materials.all_document_ids(session, base_id)
        structural = (base.embed_model_id, base.chunk_size, base.chunk_overlap) != (
            body.embed_model_id,
            body.chunk_size,
            body.chunk_overlap,
        )
        active = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        stale = active is not None and active.fingerprint != binding_fingerprint(binding)
        rebuild = bool(ids) and (structural or stale)
        if rebuild and not body.confirm_rebuild:
            raise AppError('knowledge_rebuild_confirmation_required', '更换向量模型或切片策略需要确认重新处理资料', 409)
        if body.name is not None:
            base.name = body.name
        if body.description is not None:
            base.description = body.description
        base.rerank_model_id = body.rerank_model_id if not body.use_default_rerank else None
        base.use_default_rerank = body.use_default_rerank
        base.chunk_size, base.chunk_overlap = body.chunk_size, body.chunk_overlap
        base.version, base.updated_at = base.version + 1, utcnow()
        job = None
        if rebuild:
            job = await self.queue(
                session,
                principal,
                base_id,
                operation='rebuild',
                request_id=body.client_request_id,
                value=value,
                binding=binding,
                selection=BuildSelection(document_ids=ids),
            )
        elif not ids:
            if active is not None:
                active.status, active.gc_after = 'retired', utcnow()
                base.active_revision = None
            base.embed_model_id, base.last_error = body.embed_model_id, None
        await session.flush()
        return await self.catalog.info(session, principal, base), job

    async def existing_job(
        self, session: AsyncSession, principal: Principal, request_id: UUID, value: str
    ) -> JobInfo | None:
        key = int.from_bytes(hashlib.sha256(principal.id.bytes + request_id.bytes).digest()[:8], 'big', signed=True)
        await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
        row = await session.scalar(
            select(KnowledgeJob).where(
                KnowledgeJob.actor_id == principal.id, KnowledgeJob.client_request_id == request_id
            )
        )
        if row is not None and row.fingerprint != value:
            raise AppError('idempotency_conflict', '请求标识已用于其他内容', 409)
        return job_info(row) if row is not None else None

    async def queue(  # noqa: PLR0912, PLR0915 - lock and freeze native admission facts atomically
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        *,
        operation: str,
        request_id: UUID,
        value: str,
        binding: ModelBinding,
        selection: BuildSelection,
    ) -> JobInfo:
        file_ids, document_ids, folder_id = selection.file_ids, selection.document_ids, selection.folder_id
        chunk_size, chunk_overlap = selection.chunk_size, selection.chunk_overlap
        existing = await self.existing_job(session, principal, request_id, value)
        if existing is not None:
            await self.catalog.require(session, principal, base_id, manage=True, include_deleted=True)
            return existing
        base = await self.catalog.require(
            session, principal, base_id, manage=True, lock=True, include_deleted=operation == 'delete_base'
        )
        self.catalog.idle(base)
        await self.materials.validate_import_policy(session, base, file_ids or [])
        chunk_size = base.chunk_size if chunk_size is None else chunk_size
        chunk_overlap = base.chunk_overlap if chunk_overlap is None else chunk_overlap
        active = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        if (
            active
            and active.row_count != 0
            and operation in {'import', 'rechunk'}
            and active.fingerprint != binding_fingerprint(binding)
        ):
            raise AppError('knowledge_reindex_required', 'Embedding绑定已变化，请先显式全库重建', 409)
        await self.catalog.folder(session, base_id, folder_id)
        owner = await self.identity.principal_for_user(session, base.owner_id)
        docs = []
        existing_docs = []
        for file_id in file_ids or []:
            file = await self.files.require(session, owner, file_id, active=True, lock=True)
            if file.source_file_id is not None:
                raise AppError('invalid_source', '请使用原始受管文件', 422)
            uploaded = await session.scalar(select(KnowledgeUpload).where(KnowledgeUpload.file_id == file_id))
            if uploaded is not None and (uploaded.base_id != base_id or uploaded.folder_id != folder_id):
                raise AppError('invalid_source', '上传不属于此知识库或文件夹', 422)
            material = await session.get(KnowledgeMaterial, file.id) if base.document_policy == 'library' else None
            if material is not None and material.document_id is not None:
                existing_docs.append(await self.materials.document(session, base_id, material.document_id))
                continue
            row = KnowledgeDocument(
                base_id=base_id,
                file_id=file.id,
                origin_file_id=uploaded.origin_file_id if uploaded else None,
                folder_id=folder_id,
                file_name=file.original_filename,
                file_size=file.size,
                mime=file.mime,
                source_sha256=file.sha256,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            session.add(row)
            await session.flush()
            if base.document_policy == 'library':
                material = await session.get(KnowledgeMaterial, file.id)
                if material is None:
                    raise AppError('invalid_library_source', '知识库资料需要独立副本', 422)
                material.document_id = row.id
            snapshot = await self.files.source_snapshot(session, owner, file.id)
            await self.files.references.bind_sources(
                session, owner, (snapshot,), resource_type='knowledge_document', resource_id=row.id
            )
            if uploaded:
                await self.files.references.release_resources(
                    session, resource_type='knowledge_upload', resource_ids=(uploaded.id,), actor_id=principal.id
                )
                await self.materials.finish_upload(session, uploaded.id, 'consumed')
                await session.delete(uploaded)
            docs.append(row)
        for document_id in document_ids or []:
            row = await self.materials.document(
                session, base_id, document_id, include_deleted=operation.startswith('delete')
            )
            docs.append(row)
        if not docs and existing_docs:
            # Persist the new request identity without creating documents or model work.
            job = KnowledgeJob(
                base_id=base_id,
                actor_id=principal.id,
                client_request_id=request_id,
                fingerprint=value,
                operation=operation,
                status='completed',
                phase='completed',
                index_revision=base.active_revision or uuid4(),
                finished_at=utcnow(),
                documents=[
                    DocumentResult(
                        document_id=row.id, file_id=row.file_id, status='ready', chunk_count=row.chunk_count
                    ).model_dump(mode='json')
                    for row in existing_docs
                ],
            )
            session.add(job)
            await session.flush()
            return job_info(job)
        job = KnowledgeJob(
            base_id=base_id,
            actor_id=principal.id,
            client_request_id=request_id,
            fingerprint=value,
            operation=operation,
            attempt_id=uuid4(),
            index_revision=uuid4(),
        )
        session.add(job)
        await session.flush()
        for row in docs:
            row.job_id, row.status = job.id, 'deleting' if operation.startswith('delete') else 'processing'
            if operation in {'import', 'rechunk', 'rebuild'}:
                row.chunk_size, row.chunk_overlap = chunk_size, chunk_overlap
            row.version += 1
            row.updated_at = utcnow()
        job.documents = [
            DocumentResult(document_id=row.id, file_id=row.file_id, status='pending').model_dump(mode='json')
            for row in docs
        ]
        if operation.startswith('delete'):
            await self._tombstone(session, principal, base, job, docs)
        generation = IndexGeneration(
            id=job.index_revision,
            base_id=base_id,
            attempt_id=job.attempt_id,
            collection_name=f'zh_k_{base_id.hex}_{job.index_revision.hex}_{job.attempt_id.hex}',
            binding=binding.model_dump(mode='json'),
            fingerprint=binding_fingerprint(binding),
            retrieval_version=2,
            dimension=active.dimension
            if active is not None and operation != 'rebuild' and active.fingerprint == binding_fingerprint(binding)
            else None,
        )
        session.add(generation)
        base.active_job_id, base.updated_at = job.id, utcnow()
        await session.flush()
        return job_info(job)

    async def _tombstone(
        self,
        session: AsyncSession,
        principal: Principal,
        base: KnowledgeBase,
        job: KnowledgeJob,
        docs: list[KnowledgeDocument],
    ) -> None:
        base_id = base.id
        job.phase = 'write'
        job.documents = [{**entry, 'status': 'ready', 'chunk_count': 0} for entry in job.documents]
        for row in docs:
            row.deleted_at = utcnow()
            await self.materials.retire_material(session, row.file_id, actor_id=principal.id)
        if job.operation == 'delete_base':
            base.deleted_at = utcnow()
            await retire_base_graph(session, base.id)
            for folder in await session.scalars(select(KnowledgeFolder).where(KnowledgeFolder.base_id == base_id)):
                folder.deleted_at = utcnow()
            await self.materials.release_uploads(session, principal, base_id)

    async def get_job(self, session: AsyncSession, principal: Principal, job_id: UUID) -> KnowledgeJob:
        job = await session.get(KnowledgeJob, job_id)
        if job is None:
            raise AppError('knowledge_job_not_found', '知识任务不存在', 404)
        await self.catalog.require(session, principal, job.base_id, manage=True, include_deleted=True)
        return job

    async def get_job_info(self, session: AsyncSession, principal: Principal, job_id: UUID) -> JobInfo:
        return job_info(await self.get_job(session, principal, job_id))

    async def cancel_job(self, session: AsyncSession, principal: Principal, job_id: UUID) -> JobInfo:
        job = await session.scalar(
            select(KnowledgeJob)
            .where(KnowledgeJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise AppError('knowledge_job_not_found', '知识任务不存在', 404)
        await self.catalog.require(session, principal, job.base_id, manage=True, include_deleted=True)
        if job.status not in JOB_TERMINAL and job.status != 'cancel_requested':
            job.status, job.version, job.updated_at = 'cancel_requested', job.version + 1, utcnow()
        return job_info(job)

    async def jobs(self, session: AsyncSession, principal: Principal, base_id: UUID) -> list[JobInfo]:
        await self.catalog.require(session, principal, base_id, manage=True, include_deleted=True)
        return [
            job_info(row)
            for row in await session.scalars(
                select(KnowledgeJob)
                .where(KnowledgeJob.base_id == base_id)
                .order_by(KnowledgeJob.created_at.desc())
                .limit(50)
            )
        ]
