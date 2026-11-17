"""File recycle, retention, expiry reminders and business-source retirement."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from sqlalchemy import DateTime, delete, func, or_, select, text

from zhiheng.modules.files.contracts import (
    FileInfo,
    FilePatch,
    PolicyInfo,
    PolicyRequest,
    ReminderAction,
    ReminderInfo,
    RetentionPolicyInfo,
    RetentionPolicyRequest,
)
from zhiheng.modules.files.orm import (
    FilePolicy,
    FileRecord,
    FileReference,
    FileReminder,
    FileRetentionPolicy,
)
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.files.service import FileService
    from zhiheng.shared.principal import Principal


class FileLifecycle:
    def __init__(self, files: FileService) -> None:
        self.files = files

    async def remove(
        self,
        session: AsyncSession,
        principal: Principal,
        file_id: UUID,
        expected_version: int,
        *,
        restore: bool = False,
    ) -> FileInfo:
        row = await self.files._require(session, principal, file_id, manage=True, lock=True)
        self.files.check_version(row, expected_version)
        if restore and row.status != 'deleted':
            raise AppError('file_not_deleted', '文件不在回收站', 409)
        if not restore:
            if row.status != 'active':
                raise AppError('file_not_active', '只能删除活跃文件', 409)
            ids = await self.files._lock_family(session, row)
            references = await session.scalar(
                select(func.count()).select_from(FileReference).where(FileReference.file_id.in_(ids))
            )
            if references:
                raise AppError('file_referenced', '文件仍被业务历史引用，不能删除', 409)
        row.status = 'active' if restore else 'deleted'
        row.deleted_at = None if restore else utcnow()
        policy = await self._policy(session)
        row.retention_until = None if restore else utcnow() + timedelta(days=policy.retention_days)
        self.files.touch(row, acl=True)
        self.files.audit(session, file_id, principal.id, 'restored' if restore else 'deleted')
        return await self.files.info(session, row)

    async def policy(self, session: AsyncSession, principal: Principal) -> PolicyInfo:
        principal.require_permission('ai:storage:read')
        return await self._policy(session)

    async def _policy(self, session: AsyncSession) -> PolicyInfo:
        row = await session.get(FilePolicy, 1)
        return PolicyInfo(
            retention_days=row.retention_days if row else 30,
            reminder_days=row.reminder_days if row else 7,
            version=row.version if row else 1,
        )

    async def set_policy(self, session: AsyncSession, principal: Principal, body: PolicyRequest) -> PolicyInfo:
        principal.require_permission('ai:storage:configure')

        await session.execute(text('SELECT pg_advisory_xact_lock(784246992)'))
        row = await session.get(FilePolicy, 1)
        if row is None:
            row = FilePolicy(id=1, retention_days=30, reminder_days=7, version=1)
            session.add(row)
        if row.version != body.expected_version:
            raise AppError('file_version_conflict', '文件策略已被修改', 409)
        row.retention_days = body.retention_days
        row.reminder_days = body.reminder_days
        row.version += 1
        await session.flush()
        return await self._policy(session)

    async def retention_policies(self, session: AsyncSession, principal: Principal) -> list[RetentionPolicyInfo]:
        principal.require_permission('ai:storage:read')
        return [
            self.retention_info(r)
            for r in await session.scalars(
                select(FileRetentionPolicy).order_by(FileRetentionPolicy.resource_type).limit(100)
            )
        ]

    @staticmethod
    def retention_info(row: FileRetentionPolicy) -> RetentionPolicyInfo:
        return RetentionPolicyInfo(
            resource_type=row.resource_type,
            retention_days=row.retention_days,
            enabled=row.enabled,
            remark=row.remark,
            version=row.version,
            updated_at=row.updated_at,
        )

    async def set_retention(
        self,
        session: AsyncSession,
        principal: Principal,
        body: RetentionPolicyRequest,
        *,
        expected_version: int | None = None,
        remove: bool = False,
    ) -> RetentionPolicyInfo:
        principal.require_permission('ai:storage:configure')

        await session.execute(text('SELECT pg_advisory_xact_lock(784246992)'))
        row = await session.get(FileRetentionPolicy, body.resource_type)
        if expected_version is None:
            if row is not None:
                raise AppError('policy_exists', '保留策略已存在', 409)
            row = FileRetentionPolicy(**body.model_dump(), version=1, updated_at=utcnow())
            session.add(row)
        else:
            if row is None or row.version != expected_version:
                raise AppError('file_version_conflict', '保留策略已变更', 409)
            for field in ('retention_days', 'enabled', 'remark'):
                setattr(row, field, getattr(body, field))
            row.version += 1
            row.updated_at = utcnow()
        value = self.retention_info(row)
        if remove:
            await session.delete(row)
        await session.flush()
        return value

    @staticmethod
    def _reminder_current(reminder: FileReminder, row: FileRecord) -> bool:
        if reminder.kind != 'expiry' or row.expires_at is None or row.status not in {'active', 'deleted'}:
            return False
        try:
            expiry = datetime.fromisoformat(reminder.expiry_key.replace('Z', '+00:00'))
        except ValueError:
            return False
        return expiry.utcoffset() is not None and expiry == row.expires_at

    @staticmethod
    def _reminder_info(reminder: FileReminder, row: FileRecord, reference_count: int) -> ReminderInfo:
        if row.expires_at is None:
            raise AppError('reminder_obsolete', '提醒已失效', 404)
        kind: Literal['expired', 'expiring'] = 'expired' if row.expires_at <= utcnow() else 'expiring'
        return ReminderInfo(
            id=reminder.id,
            file_id=row.id,
            kind=kind,
            is_read=reminder.is_read,
            created_at=reminder.created_at,
            original_filename=row.original_filename,
            owner_id=row.owner_id,
            expires_at=row.expires_at,
            reference_count=reference_count,
            read_by=reminder.read_by,
            read_at=reminder.read_at,
        )

    async def reminders(self, session: AsyncSession, principal: Principal) -> list[ReminderInfo]:
        query = (
            select(FileReminder, FileRecord)
            .join(FileRecord, FileRecord.id == FileReminder.file_id)
            .where(
                FileReminder.kind == 'expiry',
                FileRecord.expires_at.is_not(None),
                FileRecord.status.in_(['active', 'deleted']),
                FileReminder.expiry_key.cast(DateTime(timezone=True)) == FileRecord.expires_at,
            )
        )
        query = query.where(FileRecord.owner_id == principal.id)
        rows = await session.execute(query.order_by(FileReminder.created_at.desc()).limit(100))
        result = []
        for reminder, row in rows:
            count = (
                await session.scalar(
                    select(func.count()).select_from(FileReference).where(FileReference.file_id == row.id)
                )
                or 0
            )
            result.append(self._reminder_info(reminder, row, count))
        return result

    async def read_reminder(self, session: AsyncSession, principal: Principal, reminder_id: UUID) -> ReminderInfo:
        reminder = await session.scalar(select(FileReminder).where(FileReminder.id == reminder_id).with_for_update())
        if reminder is None:
            raise AppError('reminder_not_found', '提醒不存在', 404)
        file = await self.files._require(session, principal, reminder.file_id, manage=True)
        if not self._reminder_current(reminder, file):
            raise AppError('reminder_obsolete', '提醒已失效', 404)
        reminder.is_read = True
        reminder.read_by = principal.id
        reminder.read_at = utcnow()
        await session.flush()
        count = (
            await session.scalar(
                select(func.count()).select_from(FileReference).where(FileReference.file_id == file.id)
            )
            or 0
        )
        return self._reminder_info(reminder, file, count)

    async def extend(
        self, session: AsyncSession, principal: Principal, file_id: UUID, body: ReminderAction
    ) -> FileInfo:
        if body.expires_at.utcoffset() is None or body.expires_at <= utcnow():
            raise AppError('invalid_expiry', '延期必须是未来的带时区时间', 422)
        info = await self.files.patch(
            session, principal, file_id, FilePatch(expected_version=body.expected_version, expires_at=body.expires_at)
        )
        self.files.audit(session, file_id, principal.id, 'expiry_extended', {'reason': body.reason})
        return info

    async def dispose_expired(
        self, session: AsyncSession, principal: Principal, file_id: UUID, expected_version: int
    ) -> FileInfo:
        row = await self.files._require(session, principal, file_id, manage=True, lock=True)
        self.files.check_version(row, expected_version)
        if row.status not in {'active', 'deleted'}:
            raise AppError('file_not_mutable', '文件状态不允许到期处置', 409)
        now = utcnow()
        if row.expires_at is None or row.expires_at > now:
            raise AppError('file_not_expired', '文件尚未到期', 409)
        ids = await self.files._lock_family(session, row)
        remaining = await session.scalar(
            select(func.count())
            .select_from(FileReference)
            .where(
                FileReference.file_id.in_(ids),
                or_(FileReference.retain_until.is_(None), FileReference.retain_until > now),
            )
        )
        if remaining:
            raise AppError('file_referenced', '文件仍有永久或未到期引用', 409)
        expired = (
            await session.scalar(
                select(func.count())
                .select_from(FileReference)
                .where(FileReference.file_id.in_(ids), FileReference.retain_until <= now)
            )
            or 0
        )
        await session.execute(
            delete(FileReference).where(FileReference.file_id.in_(ids), FileReference.retain_until <= now)
        )
        self.files.audit(session, file_id, principal.id, 'expired_references_released', {'count': expired})
        if row.status == 'deleted':
            # Expiry sweep already fixed this recycle deadline; disposal must not restart its retention period.
            self.files.touch(row)
            return await self.files.info(session, row)
        return await self.remove(session, principal, file_id, expected_version)

    async def retire_business_source(self, session: AsyncSession, file_id: UUID, *, actor_id: UUID) -> None:
        """Business removal denies new reads while ordinary retention and references protect stored bytes."""
        row = await session.scalar(select(FileRecord).where(FileRecord.id == file_id).with_for_update())
        if row is None or row.status != 'active':
            return
        if row.purpose != 'knowledge_document' or row.source_file_id is not None:
            raise AppError('invalid_business_source', '只能回收业务拥有的知识库资料副本', 409)
        ids = await self.files._lock_family(session, row)
        policy = await self._policy(session)
        now = utcnow()
        records = await session.scalars(select(FileRecord).where(FileRecord.id.in_(ids)))
        for record in records:
            if record.status == 'active':
                record.status, record.deleted_at = 'deleted', now
                record.retention_until = now + timedelta(days=policy.retention_days)
                self.files.touch(record, acl=True)
                self.files.audit(session, record.id, actor_id, 'business_retired')
