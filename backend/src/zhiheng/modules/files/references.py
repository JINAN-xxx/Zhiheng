"""Business reference admission and release share the original source-first locks."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, or_, select

from zhiheng.modules.files.contracts import (
    FileInfo,
    PreparedAttachment,
    ReferenceInfo,
    SourceSnapshot,
)
from zhiheng.modules.files.orm import (
    FileRecord,
    FileReference,
    FileRetentionPolicy,
)
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.files.service import FileService
    from zhiheng.shared.principal import Principal


class FileReferences:
    def __init__(self, files: FileService) -> None:
        self.files = files

    async def require_sent_attachment(
        self, session: AsyncSession, principal: Principal, file_id: UUID, *, lock: bool = False
    ) -> FileInfo:
        row = await self.files._require(session, principal, file_id, active=True, lock=lock)
        if row.owner_id != principal.id or row.source_file_id is not None:
            raise AppError('attachment_not_owned', '只能加入自己拥有的原附件', 404)
        sent = await session.scalar(
            select(FileReference.id)
            .where(
                FileReference.file_id == file_id,
                FileReference.resource_type == 'chat',
                or_(FileReference.retain_until.is_(None), FileReference.retain_until > utcnow()),
            )
            .limit(1)
        )
        if sent is None:
            raise AppError('attachment_not_sent', '只能加入已发送消息中的附件', 422)
        return await self.files.info(session, row)

    async def bind_sources(
        self,
        session: AsyncSession,
        principal: Principal,
        sources: tuple[SourceSnapshot, ...],
        *,
        resource_type: str,
        resource_id: UUID,
    ) -> None:
        await self.files._lock_libraries(session, principal, tuple(source.file_id for source in sources))
        for source in sorted(sources, key=lambda item: item.file_id):
            current = await self.files.source_snapshot(
                session, principal, source.file_id, lock=True, artifact_file_id=source.artifact_file_id
            )
            if current != source:
                raise AppError('source_snapshot_changed', '知识来源已变化', 409)
            for file_id in (source.file_id, source.artifact_file_id):
                if file_id is None:
                    continue
                found = await session.scalar(
                    select(FileReference.id).where(
                        FileReference.file_id == file_id,
                        FileReference.resource_type == resource_type,
                        FileReference.resource_id == resource_id,
                    )
                )
                if found is None:
                    session.add(
                        FileReference(
                            file_id=file_id, resource_type=resource_type, resource_id=resource_id, retain_until=None
                        )
                    )
        await session.flush()

    async def list(self, session: AsyncSession, principal: Principal, file_id: UUID) -> list[ReferenceInfo]:
        await self.files._require(session, principal, file_id, manage=True)
        return [
            ReferenceInfo.model_validate(r)
            for r in await session.scalars(
                select(FileReference)
                .where(FileReference.file_id == file_id)
                .order_by(FileReference.created_at.desc())
                .limit(100)
            )
        ]

    async def attach(
        self,
        session: AsyncSession,
        principal: Principal,
        attachments: tuple[PreparedAttachment, ...],
        *,
        resource_type: str,
        resource_id: UUID,
    ) -> None:
        await self.files._lock_libraries(session, principal, tuple(item.summary.file_id for item in attachments))
        policy = await session.get(FileRetentionPolicy, resource_type)
        retain_until = utcnow() + timedelta(days=policy.retention_days) if policy and policy.enabled else None
        for item in sorted(attachments, key=lambda v: v.summary.file_id):
            row = await self.files._require(session, principal, item.summary.file_id, active=True, lock=True)
            if row.version != item.version or row.acl_version != item.acl_version or row.sha256 != item.summary.sha256:
                raise AppError('file_snapshot_changed', '附件已变化，请重试', 409)
            ids = [row.id]
            if item.summary.artifact_file_id:
                ids.append(item.summary.artifact_file_id)
            for file_id in ids:
                # Parent source lock serializes purge and all derived attachment references.
                artifact = await session.get(FileRecord, file_id)
                if artifact is None or artifact.status != 'active':
                    raise AppError('file_snapshot_changed', '附件已变化，请重试', 409)
                existing = await session.scalar(
                    select(FileReference.id).where(
                        FileReference.file_id == file_id,
                        FileReference.resource_type == resource_type,
                        FileReference.resource_id == resource_id,
                    )
                )
                if resource_type == 'chat' and row.purpose == 'chat_attachment':
                    artifact.expires_at = None
                if existing is None:
                    session.add(
                        FileReference(
                            file_id=file_id,
                            resource_type=resource_type,
                            resource_id=resource_id,
                            retain_until=retain_until,
                        )
                    )
        await session.flush()

    async def release_resources(
        self, session: AsyncSession, *, resource_type: str, resource_ids: tuple[UUID, ...], actor_id: UUID
    ) -> None:
        if not resource_ids:
            return
        file_ids = select(FileReference.file_id).where(
            FileReference.resource_type == resource_type, FileReference.resource_id.in_(resource_ids)
        )
        rows = list(
            await session.scalars(
                select(FileRecord)
                .where(FileRecord.id.in_(file_ids))
                .order_by(
                    func.coalesce(FileRecord.source_file_id, FileRecord.id),
                    FileRecord.source_file_id.nullsfirst(),
                    FileRecord.id,
                )
                .with_for_update()
            )
        )
        await session.execute(
            delete(FileReference).where(
                FileReference.resource_type == resource_type, FileReference.resource_id.in_(resource_ids)
            )
        )
        for row in rows:
            self.files.audit(session, row.id, actor_id, 'references_released', {'resource_type': resource_type})
