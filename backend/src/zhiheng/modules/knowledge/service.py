"""Knowledge ownership, metadata and immutable build admission facts."""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.files.public import AclEntry, BuiltinPermission, FileInfo, FileService
from zhiheng.modules.identity.public import IdentityInfo, IdentityService
from zhiheng.modules.knowledge.contracts import (
    JOB_TERMINAL,
    BaseAccess,
    BaseAcl,
    BaseAclInfo,
    BaseCreate,
    BaseInfo,
    BasePatch,
    DocumentInfo,
    DocumentPage,
    DocumentResult,
    FolderCreate,
    FolderInfo,
    FolderPatch,
    JobInfo,
)
from zhiheng.modules.knowledge.orm import (
    IndexGeneration,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeFolder,
    KnowledgeGrant,
    KnowledgeJob,
    KnowledgeUpload,
)
from zhiheng.modules.models.public import ModelBinding, ModelService
from zhiheng.runtime.contracts import RunError
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


@dataclass(frozen=True)
class BuildSelection:
    file_ids: list[UUID] | None = None
    document_ids: list[UUID] | None = None
    folder_id: UUID | None = None
    chunk_size: int = 1000
    chunk_overlap: int = 200


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, default=str, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    ).hexdigest()


def binding_fingerprint(binding: ModelBinding) -> str:
    return fingerprint(binding.model_dump(mode='json', exclude={'max_output_tokens', 'vision_enabled'}))


class KnowledgeService:
    def __init__(self, files: FileService, identity: IdentityService, models: ModelService) -> None:
        self.files, self.identity, self.models = files, identity, models

    async def references_model(self, session: AsyncSession, model_id: UUID) -> bool:
        return (
            await session.scalar(select(KnowledgeBase.id).where(KnowledgeBase.embed_model_id == model_id).limit(1))
            is not None
        )

    async def deletion_binding(self, session: AsyncSession, base_id: UUID) -> ModelBinding:
        base = await session.get(KnowledgeBase, base_id)
        assert base is not None
        retained = await self.models.retained_binding(session, base.embed_model_id)
        active = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        return ModelBinding.model_validate(active.binding) if active is not None else retained

    @staticmethod
    def manages(principal: Principal, base: KnowledgeBase) -> bool:
        return principal.is_admin or principal.id == base.owner_id

    async def require(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        *,
        manage: bool = False,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> KnowledgeBase:
        query = select(KnowledgeBase).where(KnowledgeBase.id == base_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        base = await session.scalar(query)
        if base is None or (base.deleted_at is not None and not include_deleted):
            raise AppError('knowledge_not_found', '知识库不存在', 404)
        allowed = self.manages(principal, base)
        if not allowed and not manage:
            grant = await session.get(KnowledgeGrant, (base.id, principal.id))
            effective = grant is not None and (grant.expires_at is None or grant.expires_at > utcnow())
            allowed = not (effective and grant and grant.effect == 'deny') and (
                base.access == 'public' or bool(effective and grant and grant.effect == 'allow')
            )
        if not allowed:
            raise AppError('knowledge_not_found', '知识库不存在', 404)
        return base

    async def authorize_base(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        *,
        manage: bool = False,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> BaseAccess:
        base = await self.require(
            session, principal, base_id, manage=manage, lock=lock, include_deleted=include_deleted
        )
        return BaseAccess(base.id, base.owner_id, base.embed_model_id, base.version, base.active_job_id)

    async def detail(self, session: AsyncSession, principal: Principal, base_id: UUID) -> BaseInfo:
        return await self.info(session, principal, await self.require(session, principal, base_id))

    async def info(self, session: AsyncSession, principal: Principal, base: KnowledgeBase) -> BaseInfo:
        count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(KnowledgeDocument.base_id == base.id, KnowledgeDocument.deleted_at.is_(None))
        )
        status = (
            'building'
            if base.active_job_id
            else 'ready'
            if base.active_revision
            else 'failed'
            if base.last_error
            else 'empty'
        )
        if base.active_revision:
            generation = await session.get(IndexGeneration, base.active_revision)
            try:
                binding = await self.models.resolve(session, base.embed_model_id, lock=False)
                if generation is None or binding_fingerprint(binding) != generation.fingerprint:
                    status = 'reindex_required'
            except AppError:
                status = 'reindex_required'
        return BaseInfo(
            id=base.id,
            name=base.name,
            description=base.description,
            owner_id=base.owner_id,
            access=cast("Literal['private', 'public']", base.access),
            version=base.version,
            acl_version=base.acl_version,
            embed_model_id=base.embed_model_id,
            chunk_size=base.chunk_size,
            chunk_overlap=base.chunk_overlap,
            active_revision=base.active_revision,
            active_job_id=base.active_job_id,
            index_status=status,
            document_count=count or 0,
            last_error=RunError.model_validate(base.last_error) if base.last_error else None,
            enable_knowledge_graph=False,
            llm_model_id=None,
            graph_status='disabled',
            created_at=base.created_at,
            updated_at=base.updated_at,
            can_manage=self.manages(principal, base),
        )

    async def list_bases(
        self, session: AsyncSession, principal: Principal, *, available: bool = False
    ) -> list[BaseInfo]:
        result = []
        for base in await session.scalars(
            select(KnowledgeBase).where(KnowledgeBase.deleted_at.is_(None)).order_by(KnowledgeBase.created_at.desc())
        ):
            try:
                await self.require(session, principal, base.id)
            except AppError:
                continue
            info = await self.info(session, principal, base)
            if not available or (base.active_revision is not None and info.index_status != 'reindex_required'):
                result.append(info)
        return result

    async def create(
        self, session: AsyncSession, principal: Principal, body: BaseCreate, binding: ModelBinding
    ) -> BaseInfo:
        if binding.model_type != 'embedding':
            raise AppError('embedding_model_required', '请选择Embedding模型', 422)
        if body.enable_knowledge_graph or body.llm_model_id:
            raise AppError('graph_pending', '图谱功能将在后续阶段提供', 422)
        row = KnowledgeBase(
            owner_id=principal.id, **body.model_dump(exclude={'enable_knowledge_graph', 'llm_model_id'})
        )
        session.add(row)
        await session.flush()
        return await self.info(session, principal, row)

    @staticmethod
    def version(actual: int, expected: int) -> None:
        if actual != expected:
            raise AppError('knowledge_version_conflict', '知识资源已更新，请刷新后重试', 409)

    @staticmethod
    def idle(base: KnowledgeBase | BaseAccess) -> None:
        if base.active_job_id is not None:
            raise AppError('knowledge_busy', '知识库仍有索引修改任务', 409)

    async def patch(self, session: AsyncSession, principal: Principal, base_id: UUID, body: BasePatch) -> BaseInfo:
        row = await self.require(session, principal, base_id, manage=True, lock=True)
        self.version(row.version, body.expected_version)
        self.idle(row)
        for key, value in body.model_dump(exclude={'expected_version'}, exclude_none=True).items():
            setattr(row, key, value)
        row.version += 1
        row.updated_at = utcnow()
        return await self.info(session, principal, row)

    async def acl(self, session: AsyncSession, principal: Principal, base_id: UUID) -> BaseAclInfo:
        row = await self.require(session, principal, base_id, manage=True)
        entries = [
            AclEntry(user_id=g.user_id, effect=cast("Literal['allow','deny']", g.effect), expires_at=g.expires_at)
            for g in await session.scalars(select(KnowledgeGrant).where(KnowledgeGrant.base_id == row.id))
        ]
        return BaseAclInfo(
            base=await self.info(session, principal, row),
            entries=entries,
            builtin_permissions=[
                BuiltinPermission(source=source, enabled=True, deny_overridable=False) for source in ('owner', 'admin')
            ],
        )

    async def set_acl(self, session: AsyncSession, principal: Principal, base_id: UUID, body: BaseAcl) -> BaseAclInfo:
        row = await self.require(session, principal, base_id, manage=True, lock=True)
        self.version(row.acl_version, body.expected_version)
        if len({entry.user_id for entry in body.entries}) != len(body.entries):
            raise AppError('invalid_acl', '授权用户不能重复', 422)
        for entry in body.entries:
            if not await self.identity.is_active(session, entry.user_id):
                raise AppError('invalid_acl', '授权用户不可用', 422)
        await session.execute(delete(KnowledgeGrant).where(KnowledgeGrant.base_id == row.id))
        session.add_all([KnowledgeGrant(base_id=row.id, **entry.model_dump()) for entry in body.entries])
        row.access, row.acl_version, row.updated_at = body.access, row.acl_version + 1, utcnow()
        await session.flush()
        return await self.acl(session, principal, base_id)

    async def users(
        self, session: AsyncSession, principal: Principal, base_id: UUID, search: str
    ) -> list[IdentityInfo]:
        await self.require(session, principal, base_id, manage=True)
        return await self.identity.directory(session, search=search)

    async def folder(
        self, session: AsyncSession, base_id: UUID, folder_id: UUID | None, *, include_deleted: bool = False
    ) -> KnowledgeFolder | None:
        if folder_id is None:
            return None
        query = select(KnowledgeFolder).where(KnowledgeFolder.id == folder_id, KnowledgeFolder.base_id == base_id)
        if not include_deleted:
            query = query.where(KnowledgeFolder.deleted_at.is_(None))
        row = await session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise AppError('folder_not_found', '文件夹不存在', 404)
        return row

    async def folders(self, session: AsyncSession, principal: Principal, base_id: UUID) -> list[FolderInfo]:
        await self.require(session, principal, base_id)
        return [
            FolderInfo.model_validate(row)
            for row in await session.scalars(
                select(KnowledgeFolder)
                .where(KnowledgeFolder.base_id == base_id, KnowledgeFolder.deleted_at.is_(None))
                .order_by(KnowledgeFolder.created_at)
            )
        ]

    async def create_folder(
        self, session: AsyncSession, principal: Principal, base_id: UUID, body: FolderCreate
    ) -> FolderInfo:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.idle(base)
        await self.folder(session, base_id, body.parent_id)
        row = KnowledgeFolder(base_id=base_id, **body.model_dump())
        session.add(row)
        await session.flush()
        return FolderInfo.model_validate(row)

    async def authorize_folder(
        self, session: AsyncSession, base_id: UUID, folder_id: UUID | None, *, include_deleted: bool = False
    ) -> FolderInfo | None:
        folder = await self.folder(session, base_id, folder_id, include_deleted=include_deleted)
        return FolderInfo.model_validate(folder) if folder is not None else None

    async def rename_folder(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_id: UUID, body: FolderPatch
    ) -> FolderInfo:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.idle(base)
        folder = await self.folder(session, base_id, folder_id)
        assert folder is not None
        self.version(folder.version, body.expected_version)
        folder.name, folder.version, folder.updated_at = body.name, folder.version + 1, utcnow()
        return FolderInfo.model_validate(folder)

    async def register_upload(
        self, session: AsyncSession, principal: Principal, base_id: UUID, file: FileInfo, folder_id: UUID | None
    ) -> UUID:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.idle(base)
        await self.folder(session, base_id, folder_id)
        row = KnowledgeUpload(base_id=base_id, file_id=file.id, folder_id=folder_id)
        session.add(row)
        await session.flush()
        owner = await self.identity.principal_for_user(session, base.owner_id)
        snapshot = await self.files.source_snapshot(session, owner, file.id)
        await self.files.bind_sources(session, owner, (snapshot,), resource_type='knowledge_upload', resource_id=row.id)
        return row.id

    async def remove_upload(self, session: AsyncSession, principal: Principal, base_id: UUID, upload_id: UUID) -> None:
        base = await self.require(session, principal, base_id, manage=True, lock=True)
        self.idle(base)
        upload = await session.scalar(
            select(KnowledgeUpload).where(KnowledgeUpload.id == upload_id, KnowledgeUpload.base_id == base_id)
        )
        if upload is None:
            raise AppError('knowledge_upload_not_found', '暂存上传不存在', 404)
        await self.files.release_resources(
            session, resource_type='knowledge_upload', resource_ids=(upload.id,), actor_id=principal.id
        )
        await session.delete(upload)

    async def document(
        self, session: AsyncSession, base_id: UUID, document_id: UUID, *, include_deleted: bool = False
    ) -> KnowledgeDocument:
        query = select(KnowledgeDocument).where(
            KnowledgeDocument.base_id == base_id, KnowledgeDocument.id == document_id
        )
        if not include_deleted:
            query = query.where(KnowledgeDocument.deleted_at.is_(None))
        row = await session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise AppError('document_not_found', '文档不存在', 404)
        return row

    async def documents(self, session: AsyncSession, principal: Principal, base_id: UUID) -> DocumentPage:
        base = await self.require(session, principal, base_id)
        items = []
        for row in await session.scalars(
            select(KnowledgeDocument).where(KnowledgeDocument.base_id == base_id).order_by(KnowledgeDocument.created_at)
        ):
            if row.deleted_at is not None and (not self.manages(principal, base) or row.status != 'deleting'):
                continue
            try:
                await self.files.require(session, principal, row.file_id, active=True)
            except AppError:
                continue
            if self.manages(principal, base) or row.active_revision:
                items.append(DocumentInfo.model_validate({**row.__dict__, 'error': row.error}))
        return DocumentPage(items=items, total=len(items))

    async def document_info(
        self, session: AsyncSession, base_id: UUID, document_id: UUID, *, include_deleted: bool = False
    ) -> DocumentInfo:
        row = await self.document(session, base_id, document_id, include_deleted=include_deleted)
        return DocumentInfo.model_validate({**row.__dict__, 'error': row.error})

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
        return self.job_info(row) if row is not None else None

    async def queue(
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
            await self.require(session, principal, base_id, manage=True, include_deleted=True)
            return existing
        base = await self.require(
            session, principal, base_id, manage=True, lock=True, include_deleted=operation == 'delete_base'
        )
        self.idle(base)
        active = await session.get(IndexGeneration, base.active_revision) if base.active_revision else None
        if active and operation in {'import', 'rechunk'} and active.fingerprint != binding_fingerprint(binding):
            raise AppError('knowledge_reindex_required', 'Embedding绑定已变化，请先显式全库重建', 409)
        await self.folder(session, base_id, folder_id)
        owner = await self.identity.principal_for_user(session, base.owner_id)
        docs = []
        for file_id in file_ids or []:
            file = await self.files.require(session, owner, file_id, active=True, lock=True)
            if file.source_file_id is not None:
                raise AppError('invalid_source', '请使用原始受管文件', 422)
            uploaded = await session.scalar(select(KnowledgeUpload).where(KnowledgeUpload.file_id == file_id))
            if uploaded is not None and (uploaded.base_id != base_id or uploaded.folder_id != folder_id):
                raise AppError('invalid_source', '上传不属于此知识库或文件夹', 422)
            row = KnowledgeDocument(
                base_id=base_id,
                file_id=file.id,
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
            snapshot = await self.files.source_snapshot(session, owner, file.id)
            await self.files.bind_sources(
                session, owner, (snapshot,), resource_type='knowledge_document', resource_id=row.id
            )
            if uploaded:
                await self.files.release_resources(
                    session, resource_type='knowledge_upload', resource_ids=(uploaded.id,), actor_id=principal.id
                )
                await session.delete(uploaded)
            docs.append(row)
        for document_id in document_ids or []:
            row = await self.document(session, base_id, document_id, include_deleted=operation.startswith('delete'))
            docs.append(row)
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
            if operation in {'import', 'rechunk'}:
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
            dimension=active.dimension if active is not None and operation != 'rebuild' else None,
        )
        session.add(generation)
        base.active_job_id, base.updated_at = job.id, utcnow()
        await session.flush()
        return self.job_info(job)

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
        if job.operation == 'delete_base':
            base.deleted_at = utcnow()
            for folder in await session.scalars(select(KnowledgeFolder).where(KnowledgeFolder.base_id == base_id)):
                folder.deleted_at = utcnow()
            await self.release_uploads(session, principal, base_id)

    @staticmethod
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

    async def get_job(self, session: AsyncSession, principal: Principal, job_id: UUID) -> KnowledgeJob:
        job = await session.get(KnowledgeJob, job_id)
        if job is None:
            raise AppError('knowledge_job_not_found', '知识任务不存在', 404)
        await self.require(session, principal, job.base_id, manage=True, include_deleted=True)
        return job

    async def get_job_info(self, session: AsyncSession, principal: Principal, job_id: UUID) -> JobInfo:
        return self.job_info(await self.get_job(session, principal, job_id))

    async def cancel_job(self, session: AsyncSession, principal: Principal, job_id: UUID) -> JobInfo:
        job = await session.scalar(
            select(KnowledgeJob)
            .where(KnowledgeJob.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise AppError('knowledge_job_not_found', '知识任务不存在', 404)
        await self.require(session, principal, job.base_id, manage=True, include_deleted=True)
        if job.status not in JOB_TERMINAL and job.status != 'cancel_requested':
            job.status, job.version, job.updated_at = 'cancel_requested', job.version + 1, utcnow()
        return self.job_info(job)

    async def all_document_ids(
        self, session: AsyncSession, base_id: UUID, *, include_tombstoned: bool = False
    ) -> list[UUID]:
        query = select(KnowledgeDocument.id).where(KnowledgeDocument.base_id == base_id)
        query = query.where(
            KnowledgeDocument.status != 'deleted' if include_tombstoned else KnowledgeDocument.deleted_at.is_(None)
        )
        return list(await session.scalars(query))

    async def release_uploads(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_ids: set[UUID] | None = None
    ) -> None:
        query = select(KnowledgeUpload).where(KnowledgeUpload.base_id == base_id)
        if folder_ids is not None:
            query = query.where(KnowledgeUpload.folder_id.in_(folder_ids))
        for upload in await session.scalars(query):
            await self.files.release_resources(
                session, resource_type='knowledge_upload', resource_ids=(upload.id,), actor_id=principal.id
            )
            await session.delete(upload)

    async def deletion_scope(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_id: UUID
    ) -> list[UUID]:
        await self.folder(session, base_id, folder_id, include_deleted=True)
        folders = list(await session.scalars(select(KnowledgeFolder).where(KnowledgeFolder.base_id == base_id)))
        ids = {folder_id}
        while True:
            expanded = ids | {folder.id for folder in folders if folder.parent_id in ids}
            if expanded == ids:
                break
            ids = expanded
        for folder in folders:
            if folder.id in ids:
                folder.deleted_at = utcnow()
        await self.release_uploads(session, principal, base_id, ids)
        return list(
            await session.scalars(
                select(KnowledgeDocument.id).where(
                    KnowledgeDocument.base_id == base_id,
                    KnowledgeDocument.folder_id.in_(ids),
                    KnowledgeDocument.status != 'deleted',
                )
            )
        )

    async def jobs(self, session: AsyncSession, principal: Principal, base_id: UUID) -> list[JobInfo]:
        await self.require(session, principal, base_id, manage=True, include_deleted=True)
        return [
            self.job_info(row)
            for row in await session.scalars(
                select(KnowledgeJob)
                .where(KnowledgeJob.base_id == base_id)
                .order_by(KnowledgeJob.created_at.desc())
                .limit(50)
            )
        ]
