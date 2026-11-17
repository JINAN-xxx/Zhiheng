"""Existing document-policy migration, with source fencing and publish-time verification."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import select

from zhiheng.modules.knowledge.contracts import (
    NATIVE_FORMATS,
    DocumentInfo,
    JobInfo,
    MigrationRequest,
)
from zhiheng.modules.knowledge.orm import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeGrant,
    KnowledgeJob,
    KnowledgeMaterial,
)
from zhiheng.modules.knowledge.orm import (
    KnowledgeMigration as MigrationRecord,
)
from zhiheng.modules.knowledge.values import BuildSelection, fingerprint
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.files.public import FileInfo
    from zhiheng.modules.knowledge.build_admission import KnowledgeBuildAdmission
    from zhiheng.modules.knowledge.materials import KnowledgeMaterials
    from zhiheng.modules.knowledge.service import KnowledgeService
    from zhiheng.shared.principal import Principal


class KnowledgeMigration:
    def __init__(
        self, catalog: KnowledgeService, materials: KnowledgeMaterials, builds: KnowledgeBuildAdmission
    ) -> None:
        self.catalog, self.materials, self.builds = catalog, materials, builds
        self.files, self.identity, self.models = catalog.files, catalog.identity, catalog.models

    async def preview(self, session: AsyncSession, principal: Principal, base_id: UUID) -> dict[str, object]:
        base = await self.catalog.require(session, principal, base_id, manage=True)
        grants = list(await session.scalars(select(KnowledgeGrant).where(KnowledgeGrant.base_id == base_id)))
        cleanup = any(grant.effect != 'allow' or grant.expires_at is not None for grant in grants)
        documents = []
        rows = await session.scalars(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.base_id == base_id,
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeDocument.migration_source_document_id.is_(None),
            )
            .order_by(KnowledgeDocument.created_at)
        )
        for document in rows:
            reason = None
            try:
                file = await self.files.require(session, principal, document.file_id, active=True)
                if file.owner_id != principal.id or file.source_file_id is not None:
                    reason = '资料原文不归知识库所有者所有，请先处理'
                elif file.original_filename.rpartition('.')[2].lower() not in NATIVE_FORMATS:
                    reason = '资料格式无法处理，请先移除或替换'
            except AppError:
                reason = '资料原文缺失或无读取权限，请先处理'
            documents.append(
                {
                    'document_id': str(document.id),
                    'file_name': document.file_name,
                    'can_migrate': reason is None,
                    'reason': reason,
                }
            )
        return {
            'base_id': str(base.id),
            'document_policy': base.document_policy,
            'expected_version': base.version,
            'acl_version': base.acl_version,
            'sharing': {
                'access': base.access,
                'entries': [
                    {
                        'user_id': str(grant.user_id),
                        'effect': grant.effect,
                        'expires_at': grant.expires_at.isoformat() if grant.expires_at else None,
                    }
                    for grant in grants
                ],
            },
            'documents': documents,
            'requires_acl_cleanup': cleanup,
            'can_migrate': base.document_policy == 'legacy'
            and not cleanup
            and all(item['can_migrate'] for item in documents),
        }

    async def sources(
        self, session: AsyncSession, principal: Principal, base_id: UUID, body: MigrationRequest
    ) -> list[DocumentInfo]:
        base = await self.catalog.require(session, principal, base_id, manage=True, lock=True)
        self.catalog.version(base.version, body.expected_version)
        self.catalog.version(base.acl_version, body.expected_acl_version)
        self.catalog.idle(base)
        if not body.confirm_shared_access:
            raise AppError('migration_confirmation_required', '请确认迁移后共享用户可查看和下载全部资料', 422)
        preview = await self.preview(session, principal, base_id)
        if not preview['can_migrate']:
            raise AppError(
                'knowledge_migration_blocked', '请先处理资料所有权、缺失文件或历史共享限制', 409, details=preview
            )
        return [
            await self.materials.document_info(session, base_id, UUID(str(item['document_id'])))
            for item in cast('list[dict[str, object]]', preview['documents'])
        ]

    async def copy(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        source: DocumentInfo,
        file: FileInfo,
    ) -> UUID:
        base = await self.catalog.require(session, principal, base_id, manage=True, lock=True)
        self.catalog.idle(base)
        if base.document_policy != 'legacy':
            raise AppError('knowledge_migration_blocked', '知识库资料权限已变化', 409)
        old = await self.materials.document(session, base_id, source.id)
        row = KnowledgeDocument(
            base_id=base_id,
            file_id=file.id,
            folder_id=old.folder_id,
            migration_source_document_id=old.id,
            file_name=file.original_filename,
            file_size=file.size,
            mime=file.mime,
            source_sha256=file.sha256,
            chunk_size=old.chunk_size,
            chunk_overlap=old.chunk_overlap,
        )
        session.add(row)
        await session.flush()
        session.add(KnowledgeMaterial(base_id=base_id, file_id=file.id, document_id=row.id))
        await session.flush()
        snapshot = await self.files.source_snapshot(session, principal, file.id)
        await self.files.references.bind_sources(
            session, principal, (snapshot,), resource_type='knowledge_document', resource_id=row.id
        )
        return row.id

    async def existing_copy(
        self, session: AsyncSession, base_id: UUID, source: DocumentInfo, source_sha256: str
    ) -> UUID | None:
        return cast(
            'UUID | None',
            await session.scalar(
                select(KnowledgeDocument.id)
                .where(
                    KnowledgeDocument.base_id == base_id,
                    KnowledgeDocument.migration_source_document_id == source.id,
                    KnowledgeDocument.source_sha256 == source_sha256,
                    KnowledgeDocument.deleted_at.is_(None),
                )
                .order_by(KnowledgeDocument.created_at.desc())
                .limit(1)
            ),
        )

    async def queue(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        body: MigrationRequest,
        document_ids: list[UUID],
        sources: list[dict[str, object]],
    ) -> JobInfo:
        await self.sources(session, principal, base_id, body)
        base = await self.catalog.require(session, principal, base_id, manage=True)
        binding = await self.models.bindings.resolve(session, base.embed_model_id)
        job = await self.builds.queue(
            session,
            principal,
            base_id,
            operation='rebuild',
            request_id=body.client_request_id,
            value=fingerprint(['migration', str(base_id), body.model_dump(mode='json')]),
            binding=binding,
            selection=BuildSelection(document_ids=document_ids),
        )
        session.add(MigrationRecord(base_id=base_id, job_id=job.id, acl_version=base.acl_version, documents=sources))
        await session.flush()
        return job

    async def validate(self, session: AsyncSession, job_id: UUID, *, lock: bool = False) -> None:
        migration = await session.scalar(select(MigrationRecord).where(MigrationRecord.job_id == job_id))
        if migration is None or migration.status != 'pending':
            return
        base_query = select(KnowledgeBase).where(KnowledgeBase.id == migration.base_id)
        base = await session.scalar(base_query)
        if lock and base is not None:
            owner = await self.identity.principal_for_user(session, base.owner_id)
            await self.materials.lock_library_sources(
                session,
                owner,
                tuple(UUID(str(item['source_file_id'])) for item in migration.documents),
                additional_base_ids=(base.id,),
            )
            base = await session.scalar(base_query.with_for_update().execution_options(populate_existing=True))
            # Publish follows the same all bases -> job -> all sources order as worker admission.
            await session.execute(select(KnowledgeJob.id).where(KnowledgeJob.id == job_id).with_for_update())
        if (
            base is None
            or base.deleted_at is not None
            or base.document_policy != 'legacy'
            or base.acl_version != migration.acl_version
        ):
            raise AppError('knowledge_migration_stale', '迁移期间共享范围已变化，保留原资料权限', 409)
        owner = await self.identity.principal_for_user(session, base.owner_id)
        for item in sorted(migration.documents, key=lambda source: str(source['source_file_id'])):
            snapshot = await self.files.source_snapshot(session, owner, UUID(str(item['source_file_id'])), lock=lock)
            if snapshot.model_dump(mode='json') != item['source_snapshot']:
                raise AppError('knowledge_migration_stale', '迁移原文或授权已变化，保留原资料权限', 409)

    async def finish(self, session: AsyncSession, job_id: UUID) -> None:
        migration = await session.scalar(select(MigrationRecord).where(MigrationRecord.job_id == job_id))
        if migration is None or migration.status != 'pending':
            return
        # Revalidate under locks in the very transaction that changes the effective document policy.
        await self.validate(session, job_id, lock=True)
        job = await session.get(KnowledgeJob, job_id)
        if job is None or job.status != 'completed':
            raise AppError('knowledge_migration_incomplete', '迁移索引未全部完成，保留原资料权限', 409)
        base = await session.get(KnowledgeBase, migration.base_id)
        assert base is not None
        for item in migration.documents:
            old = await self.materials.document(session, base.id, UUID(str(item['source_document_id'])))
            old.deleted_at, old.status, old.active_revision = utcnow(), 'deleted', None
            await self.files.references.release_resources(
                session, resource_type='knowledge_document', resource_ids=(old.id,), actor_id=base.owner_id
            )
        base.document_policy, base.acl_version = 'library', base.acl_version + 1
        migration.status = 'completed'
