"""Offline, resumable object copying. File identities and authorization metadata never change."""

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zhiheng.modules.files.jobs import LOCK_ID
from zhiheng.modules.files.orm import FileRecord
from zhiheng.shared.errors import AppError
from zhiheng.shared.storage import ImportStoragePort, StorageInspection, StorageSourcePort

if TYPE_CHECKING:
    from uuid import UUID

MIGRATION_BATCH_SIZE = 100


@dataclass(frozen=True)
class StorageMigrationReport:
    scanned: int
    copied: int
    existing: int
    total_bytes: int


class FileStorageMigration:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def run(
        self,
        source: StorageSourcePort,
        target: ImportStoragePort,
        *,
        verify_only: bool = False,
    ) -> StorageMigrationReport:
        await source.check()
        await target.check()
        scanned = copied = existing = total_bytes = 0
        async with self._sessions.begin() as session:
            if not await session.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': LOCK_ID}):
                raise AppError('file_job_busy', '文件维护或迁移正在运行，请稍后重试', 409)
            # Stop API and Worker before cutover. This lock additionally rejects concurrent metadata
            # writes while copying; source bytes and destination writes remain immutable by key.
            await session.execute(text('LOCK TABLE files IN SHARE MODE NOWAIT'))
            if await session.scalar(select(FileRecord.id).where(FileRecord.status == 'purging').limit(1)):
                raise AppError('file_migration_busy', '仍有文件正在物理清理，请完成清理后重试', 409)
            cursor: UUID | None = None
            while True:
                query = select(FileRecord).where(FileRecord.status != 'purged')
                if cursor is not None:
                    query = query.where(FileRecord.id > cursor)
                records = list(await session.scalars(query.order_by(FileRecord.id).limit(MIGRATION_BATCH_SIZE)))
                if not records:
                    break
                for record in records:
                    content = await source.read(record.storage_key)
                    expected = StorageInspection(record.size, record.sha256)
                    if StorageInspection(len(content), hashlib.sha256(content).hexdigest()) != expected:
                        raise AppError('file_integrity_error', f'来源文件完整性校验失败：{record.id}', 503)
                    changed = False
                    if not verify_only:
                        changed = await target.import_object(record.storage_key, content)
                    if await target.inspect(record.storage_key) != expected:
                        raise AppError('file_integrity_error', f'目标文件完整性校验失败：{record.id}', 503)
                    scanned += 1
                    copied += int(changed)
                    existing += int(not changed)
                    total_bytes += record.size
                cursor = records[-1].id
                # Bound ORM memory to one page even when migrating a large file catalog.
                session.expunge_all()
        return StorageMigrationReport(scanned, copied, existing, total_bytes)
