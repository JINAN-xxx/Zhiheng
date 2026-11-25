"""Knowledge material ownership, document metadata and resumable upload receipts."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

from sqlalchemy import func, or_, select, tuple_, update

from zhiheng.modules.knowledge.build_admission import job_info
from zhiheng.modules.knowledge.contracts import (
    NATIVE_FORMATS,
    AttachmentImportInfo,
    DocumentInfo,
    DocumentPage,
    JobInfo,
)
from zhiheng.modules.knowledge.orm import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeFolder,
    KnowledgeJob,
    KnowledgeMaterial,
    KnowledgeUpload,
    KnowledgeUploadReceipt,
)
from zhiheng.modules.knowledge.parsing_orm import ParsingJob
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.files.public import FileInfo, SourceSnapshot
    from zhiheng.modules.knowledge.service import KnowledgeService
    from zhiheng.shared.principal import Principal


class KnowledgeMaterials:
    def __init__(self, catalog: KnowledgeService) -> None:
        self.catalog = catalog
        self.files, self.identity = catalog.files, catalog.identity

    async def lock_library_sources(
        self,
        session: AsyncSession,
        principal: Principal,
        file_ids: tuple[UUID, ...],
        *,
        additional_base_ids: tuple[UUID, ...] = (),
    ) -> None:
        bases = set(
            await session.scalars(select(KnowledgeMaterial.base_id).where(KnowledgeMaterial.file_id.in_(file_ids)))
        ) | set(additional_base_ids)
        for base_id in sorted(bases):
            await self.catalog.require(session, principal, base_id, lock=True)

    async def authorize_library_file(
        self, session: AsyncSession, principal: Principal, file_id: UUID, *, lock: bool = False
    ) -> bool:
        material = await session.scalar(select(KnowledgeMaterial).where(KnowledgeMaterial.file_id == file_id))
        if material is None:
            return False  # Legacy originals keep their existing effective ACL.
        base = await self.catalog.require(session, principal, material.base_id, lock=lock)
        document = await session.get(KnowledgeDocument, material.document_id) if material.document_id else None
        if material.document_id is not None and (document is None or document.deleted_at is not None):
            raise AppError('document_not_found', '资料已移除或无访问权限', 404)
        if (
            document is None
            and await session.scalar(
                select(KnowledgeUpload.id).where(KnowledgeUpload.file_id == file_id, KnowledgeUpload.base_id == base.id)
            )
            is None
        ):
            raise AppError('document_not_found', '资料上传已移除', 404)
        # Staged uploads and pending migration copies are private to the owner.
        if (document is None or base.document_policy != 'library') and base.owner_id != principal.id:
            raise AppError('document_not_found', '资料不存在', 404)
        return True

    async def require_document_source(
        self, session: AsyncSession, principal: Principal, base_id: UUID, document_id: UUID, *, lock: bool = False
    ) -> FileInfo:
        await self.catalog.require(session, principal, base_id, lock=lock)
        document = await self.document(session, base_id, document_id)
        return await self.files.require(session, principal, document.file_id, active=True, lock=lock)

    async def document_source_snapshot(
        self,
        session: AsyncSession,
        principal: Principal,
        document: KnowledgeDocument | DocumentInfo,
        *,
        lock: bool = False,
    ) -> SourceSnapshot:
        await self.require_document_source(session, principal, document.base_id, document.id, lock=lock)
        return await self.files.source_snapshot(
            session,
            principal,
            document.file_id,
            lock=lock,
            artifact_file_id=getattr(document, 'artifact_file_id', None),
        )

    async def validate_import_policy(self, session: AsyncSession, base: KnowledgeBase, file_ids: list[UUID]) -> None:
        if base.document_policy != 'legacy' or not file_ids:
            return
        roots = await self.files.source_root_ids(session, tuple(file_ids))
        material = await session.scalar(
            select(KnowledgeMaterial.file_id).where(KnowledgeMaterial.file_id.in_(roots)).limit(1)
        )
        if material is not None:
            raise AppError('knowledge_migration_required', '请先迁移此知识库，再添加知识库资料副本', 409)

    async def import_sources(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        file_ids: list[UUID],
        folder_id: UUID | None,
    ) -> list[FileInfo]:
        base = await self.catalog.require(session, principal, base_id, manage=True)
        self.catalog.idle(base)
        await self.catalog.folder(session, base_id, folder_id)
        await self.validate_import_policy(session, base, file_ids)
        if base.document_policy != 'library':
            return []
        copies = []
        for file_id in file_ids:
            material = await session.get(KnowledgeMaterial, file_id)
            if material is not None and material.base_id == base_id and material.document_id is not None:
                await self.require_document_source(session, principal, base_id, material.document_id)
                continue
            uploaded = await session.scalar(select(KnowledgeUpload).where(KnowledgeUpload.file_id == file_id))
            if uploaded is not None:
                if uploaded.base_id != base_id or uploaded.folder_id != folder_id:
                    raise AppError('invalid_source', '上传不属于此知识库或文件夹', 422)
                continue
            file = await self.files.require(session, principal, file_id, active=True)
            if file.owner_id != principal.id or file.source_file_id is not None:
                raise AppError('attachment_not_owned', '只能加入自己拥有的原文件', 404)
            if file.original_filename.rpartition('.')[2].lower() not in NATIVE_FORMATS:
                raise AppError('unsupported_knowledge_format', '该格式不能加入知识库', 422)
            copies.append(file)
        return copies

    async def copied_source_file(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        source_id: UUID,
        folder_id: UUID | None,
    ) -> UUID | None:
        base = await self.catalog.require(session, principal, base_id, manage=True, lock=True)
        document = await session.scalar(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.base_id == base_id,
                KnowledgeDocument.origin_file_id == source_id,
                KnowledgeDocument.deleted_at.is_(None),
            )
            .limit(1)
        )
        if document is not None:
            return document.file_id
        self.catalog.idle(base)
        upload = await session.scalar(
            select(KnowledgeUpload)
            .where(
                KnowledgeUpload.base_id == base_id,
                KnowledgeUpload.origin_file_id == source_id,
                KnowledgeUpload.folder_id == folder_id,
            )
            .limit(1)
        )
        return upload.file_id if upload else None

    async def imported_files_job(
        self, session: AsyncSession, principal: Principal, base_id: UUID, file_ids: list[UUID]
    ) -> JobInfo | None:
        await self.catalog.require(session, principal, base_id, manage=True)
        if len(file_ids) != 1:
            return None
        material = await session.get(KnowledgeMaterial, file_ids[0])
        if material is None or material.base_id != base_id or material.document_id is None:
            return None
        document = await self.document(session, base_id, material.document_id)
        if document.job_id is None:
            return None
        job = await session.get(KnowledgeJob, document.job_id)
        return job_info(job) if job else None

    async def register_copy_upload(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        source: FileInfo,
        file: FileInfo,
        folder_id: UUID | None,
    ) -> None:
        await self.files.require(session, principal, source.id, active=True, lock=True)
        upload_id = await self.register_upload(session, principal, base_id, file, folder_id)
        upload = await session.get(KnowledgeUpload, upload_id)
        assert upload is not None
        upload.origin_file_id = source.id

    async def existing_attachment(
        self, session: AsyncSession, principal: Principal, base_id: UUID, file_id: UUID
    ) -> AttachmentImportInfo | None:
        await self.catalog.require(session, principal, base_id, manage=True, lock=True)
        row = await session.scalar(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.base_id == base_id,
                KnowledgeDocument.origin_file_id == file_id,
                KnowledgeDocument.deleted_at.is_(None),
            )
            .order_by(KnowledgeDocument.created_at)
            .limit(1)
        )
        if row is None or row.job_id is None:
            return None
        job = await session.get(KnowledgeJob, row.job_id)
        assert job is not None
        return AttachmentImportInfo(
            document=await self.document_info(session, base_id, row.id), job=job_info(job), already_exists=True
        )

    async def upload_receipt(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        request_id: UUID,
        value: str,
    ) -> dict[str, object] | None:
        await self.catalog.require(session, principal, base_id, manage=True)
        receipt = await session.get(KnowledgeUploadReceipt, (principal.id, request_id))
        if receipt is None:
            return None
        if receipt.base_id != base_id or receipt.fingerprint != value:
            raise AppError('idempotency_conflict', '上传标识已用于其他文件或知识库', 409)
        if receipt.status in {'discarded', 'expired'}:
            raise AppError('knowledge_upload_expired', '此上传已取消或过期，请重新选择文件', 409)
        file = await self.files.require(session, principal, receipt.file_id, active=True)
        return {'file': file.model_dump(mode='json'), 'upload_id': receipt.upload_id, 'folder_id': receipt.folder_id}

    async def find_upload_receipt(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        request_id: UUID,
    ) -> dict[str, object]:
        await self.catalog.require(session, principal, base_id, manage=True)
        receipt = await session.get(KnowledgeUploadReceipt, (principal.id, request_id))
        if receipt is None or receipt.base_id != base_id:
            raise AppError('knowledge_upload_not_found', '上传尚未接纳，请重新选择原文件后重试', 404)
        if receipt.status in {'discarded', 'expired'}:
            raise AppError('knowledge_upload_expired', '此上传已取消或过期，请重新选择文件', 409)
        file = await self.files.require(session, principal, receipt.file_id, active=True)
        return {'file': file.model_dump(mode='json'), 'upload_id': receipt.upload_id, 'folder_id': receipt.folder_id}

    async def remember_upload(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        request_id: UUID,
        value: str,
        file_id: UUID,
        upload_id: UUID,
        folder_id: UUID | None,
    ) -> None:
        session.add(
            KnowledgeUploadReceipt(
                owner_id=principal.id,
                request_id=request_id,
                base_id=base_id,
                file_id=file_id,
                upload_id=upload_id,
                folder_id=folder_id,
                fingerprint=value,
            )
        )
        await session.flush()

    async def finish_upload(self, session: AsyncSession, upload_id: UUID, status: str) -> None:
        await session.execute(
            update(KnowledgeUploadReceipt).where(KnowledgeUploadReceipt.upload_id == upload_id).values(status=status)
        )

    async def expired_uploads(self, session: AsyncSession, before: datetime) -> list[tuple[UUID, UUID, UUID]]:
        return [
            tuple(row)
            for row in await session.execute(
                select(KnowledgeBase.id, KnowledgeBase.owner_id, KnowledgeUpload.id)
                .join(KnowledgeUpload, KnowledgeUpload.base_id == KnowledgeBase.id)
                .where(KnowledgeUpload.created_at < before, KnowledgeBase.active_job_id.is_(None))
                .order_by(KnowledgeUpload.created_at)
                .limit(50)
            )
        ]

    async def register_upload(
        self, session: AsyncSession, principal: Principal, base_id: UUID, file: FileInfo, folder_id: UUID | None
    ) -> UUID:
        base = await self.catalog.require(session, principal, base_id, manage=True, lock=True)
        self.catalog.idle(base)
        await self.catalog.folder(session, base_id, folder_id)
        row = KnowledgeUpload(base_id=base_id, file_id=file.id, folder_id=folder_id)
        session.add(row)
        await session.flush()
        if base.document_policy == 'library':
            session.add(KnowledgeMaterial(base_id=base.id, file_id=file.id))
            await session.flush()
        owner = await self.identity.principal_for_user(session, base.owner_id)
        snapshot = await self.files.source_snapshot(session, owner, file.id)
        await self.files.references.bind_sources(
            session, owner, (snapshot,), resource_type='knowledge_upload', resource_id=row.id
        )
        return row.id

    async def retire_material(self, session: AsyncSession, file_id: UUID, *, actor_id: UUID) -> None:
        if await session.get(KnowledgeMaterial, file_id) is not None:
            await self.files.lifecycle.retire_business_source(session, file_id, actor_id=actor_id)

    async def remove_upload(self, session: AsyncSession, principal: Principal, base_id: UUID, upload_id: UUID) -> None:
        base = await self.catalog.require(session, principal, base_id, manage=True, lock=True)
        self.catalog.idle(base)
        upload = await session.scalar(
            select(KnowledgeUpload).where(KnowledgeUpload.id == upload_id, KnowledgeUpload.base_id == base_id)
        )
        if upload is None:
            raise AppError('knowledge_upload_not_found', '暂存上传不存在', 404)
        source = await self.files.require(session, principal, upload.file_id)
        if source.purpose == 'knowledge_document':
            await self.files.lifecycle.retire_business_source(session, upload.file_id, actor_id=principal.id)
        await self.files.references.release_resources(
            session, resource_type='knowledge_upload', resource_ids=(upload.id,), actor_id=principal.id
        )
        await self.finish_upload(session, upload.id, 'discarded')
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
        base = await self.catalog.require(session, principal, base_id)
        rows = (await session.execute(self._document_query(principal, base))).tuples().all()
        visible = await self._visible_documents(session, principal, base, rows)
        return DocumentPage(items=await self._document_info(session, base_id, visible), total=len(visible))

    async def visible_document_ids(
        self, session: AsyncSession, principal: Principal, base_id: UUID, document_ids: list[UUID]
    ) -> list[UUID]:
        base = await self.catalog.require(session, principal, base_id)
        query = self._document_query(principal, base).where(
            KnowledgeDocument.id.in_(document_ids), KnowledgeDocument.deleted_at.is_(None)
        )
        rows = (await session.execute(query)).tuples().all()
        return [row.id for row in await self._visible_documents(session, principal, base, rows)]

    async def document_page(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        *,
        folder_id: UUID | None,
        search: str,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[DocumentPage, tuple[datetime, UUID] | None]:
        base = await self.catalog.require(session, principal, base_id)
        query = self._document_query(principal, base).where(KnowledgeDocument.folder_id == folder_id)
        if search:
            pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            query = query.where(KnowledgeDocument.file_name.ilike(pattern, escape='\\'))
        visible: list[KnowledgeDocument] = []
        scan_size = max(32, limit + 1)
        while len(visible) <= limit:
            batch = query
            if after is not None:
                batch = batch.where(tuple_(KnowledgeDocument.created_at, KnowledgeDocument.id) > after)
            rows = (await session.execute(batch.limit(scan_size))).tuples().all()
            visible.extend(await self._visible_documents(session, principal, base, rows))
            if len(rows) < scan_size:
                break
            last = rows[-1][0]
            after = (last.created_at, last.id)
        next_position = (visible[limit - 1].created_at, visible[limit - 1].id) if len(visible) > limit else None
        items = await self._document_info(session, base_id, visible[:limit])
        return DocumentPage(items=items, total=len(items)), next_position

    def _document_query(
        self, principal: Principal, base: KnowledgeBase
    ) -> Select[tuple[KnowledgeDocument, UUID | None, UUID | None]]:
        # SQLAlchemy types the selected columns before accounting for the outer join.
        query = cast(
            'Select[tuple[KnowledgeDocument, UUID | None, UUID | None]]',
            select(KnowledgeDocument, KnowledgeMaterial.document_id, KnowledgeMaterial.base_id).outerjoin(
                KnowledgeMaterial, KnowledgeMaterial.file_id == KnowledgeDocument.file_id
            ),
        )
        query = query.where(KnowledgeDocument.base_id == base.id).order_by(
            KnowledgeDocument.created_at, KnowledgeDocument.id
        )
        if base.document_policy != 'library':
            query = query.where(KnowledgeDocument.migration_source_document_id.is_(None))
        if self.catalog.manages(principal, base):
            return query.where(or_(KnowledgeDocument.deleted_at.is_(None), KnowledgeDocument.status == 'deleting'))
        query = query.where(KnowledgeDocument.deleted_at.is_(None))
        if base.document_policy != 'library':
            query = query.where(KnowledgeDocument.active_revision.is_not(None))
        return query

    async def _visible_documents(
        self,
        session: AsyncSession,
        principal: Principal,
        base: KnowledgeBase,
        rows: Sequence[tuple[KnowledgeDocument, UUID | None, UUID | None]],
    ) -> list[KnowledgeDocument]:
        if not rows:
            return []
        library = base.document_policy == 'library'
        # An attached original in this authorized library inherits the already-checked base ACL.
        attached = (
            {
                row.file_id
                for row, document_id, material_base_id in rows
                if row.id == document_id and material_base_id == base.id and row.deleted_at is None
            }
            if library or self.catalog.manages(principal, base)
            else set()
        )
        readable = await self.files.active_original_ids(session, attached)
        visible = []
        for row, document_id, material_base_id in rows:
            if row.file_id not in readable or row.id != document_id or material_base_id != base.id:
                try:
                    await self.files.ensure_readable(session, principal, row.file_id)
                except AppError:
                    continue
            visible.append(row)
        return visible

    async def _document_info(
        self, session: AsyncSession, base_id: UUID, rows: Sequence[KnowledgeDocument]
    ) -> list[DocumentInfo]:
        artifact_ids = {row.artifact_file_id for row in rows if row.artifact_file_id is not None}
        engines: dict[UUID | None, str] = (
            dict(
                (
                    await session.execute(
                        select(ParsingJob.artifact_id, ParsingJob.engine)
                        .join(KnowledgeJob, KnowledgeJob.id == ParsingJob.parent_id)
                        .where(
                            KnowledgeJob.base_id == base_id,
                            ParsingJob.phase == 'completed',
                            ParsingJob.artifact_id.in_(artifact_ids),
                        )
                        .distinct(ParsingJob.artifact_id)
                        .order_by(ParsingJob.artifact_id, ParsingJob.updated_at.desc())
                    )
                )
                .tuples()
                .all()
            )
            if artifact_ids
            else {}
        )
        return [
            DocumentInfo.model_validate(
                {
                    **row.__dict__,
                    'error': row.error,
                    'parsing_engine': engines.get(
                        row.artifact_file_id, 'native' if row.artifact_file_id is not None else None
                    ),
                }
            )
            for row in rows
        ]

    async def document_info(
        self, session: AsyncSession, base_id: UUID, document_id: UUID, *, include_deleted: bool = False
    ) -> DocumentInfo:
        row = await self.document(session, base_id, document_id, include_deleted=include_deleted)
        return DocumentInfo.model_validate({**row.__dict__, 'error': row.error})

    async def all_document_ids(
        self, session: AsyncSession, base_id: UUID, *, include_tombstoned: bool = False
    ) -> list[UUID]:
        query = select(KnowledgeDocument.id).where(KnowledgeDocument.base_id == base_id)
        base = await session.get(KnowledgeBase, base_id)
        if base is not None and base.document_policy == 'legacy' and not include_tombstoned:
            query = query.where(KnowledgeDocument.migration_source_document_id.is_(None))
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
            await self.retire_material(session, upload.file_id, actor_id=principal.id)
            await self.files.references.release_resources(
                session, resource_type='knowledge_upload', resource_ids=(upload.id,), actor_id=principal.id
            )
            await session.delete(upload)

    async def deletion_scope(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_id: UUID
    ) -> list[UUID]:
        await self.catalog.folder(session, base_id, folder_id, include_deleted=True)
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

    async def pending_uploads(
        self, session: AsyncSession, principal: Principal, base_id: UUID, folder_id: UUID | None = None
    ) -> dict[str, object]:
        base = await self.catalog.require(session, principal, base_id, manage=True)
        self.catalog.idle(base)
        await self.catalog.folder(session, base_id, folder_id)
        query = select(KnowledgeUpload).where(
            KnowledgeUpload.base_id == base_id,
            KnowledgeUpload.folder_id == folder_id,
            KnowledgeUpload.created_at > utcnow() - timedelta(hours=24),
        )
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = await session.scalars(query.order_by(KnowledgeUpload.created_at, KnowledgeUpload.id).limit(20))
        items = []
        for row in rows:
            file = await self.files.get(session, principal, row.file_id)
            receipt = await session.scalar(
                select(KnowledgeUploadReceipt).where(KnowledgeUploadReceipt.upload_id == row.id)
            )
            items.append(
                {
                    'request_id': receipt.request_id if receipt else row.id,
                    'upload_id': row.id,
                    'file': file.model_dump(mode='json'),
                    'folder_id': row.folder_id,
                    'created_at': row.created_at,
                }
            )
        return {'items': items, 'total': total}
