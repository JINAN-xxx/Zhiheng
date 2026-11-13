"""Files-specific bounded maintenance; one PostgreSQL session lock serializes manual/periodic runs."""

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from zhiheng.modules.files.contracts import FileInfo, IssueAction, JobInfo, JobKind, ReconcileIssueInfo
from zhiheng.modules.files.orm import FileJob, FileReconcileIssue, FileRecord, FileReference, FileReminder
from zhiheng.modules.files.service import FileService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

BATCH_SIZE = 100
LOCK_ID = 784246993


class FileJobs:
    def __init__(
        self,
        files: FileService,
        sessions: async_sessionmaker[AsyncSession],
        *,
        attempt_id: UUID | None = None,
        after_purge: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        self._files, self._sessions = files, sessions
        self._attempt_id = attempt_id
        self.after_purge = after_purge

    async def purge(self, principal: Principal, file_id: UUID, expected_version: int) -> FileInfo:
        info = await self._purge_native(principal, file_id, expected_version)
        # Native metadata is committed and every File lock is released before App accounting.
        # A failed callback is safe to retry, including an already-purged native file.
        if self.after_purge is not None:
            await self.after_purge(file_id)
        return info

    async def _purge_native(self, principal: Principal, file_id: UUID, expected_version: int) -> FileInfo:
        async with self._transaction() as session:
            if self._attempt_id is not None:
                principal.require_permission('ai:storage:delete')
                row = await self._maintenance_source(session, file_id)
            else:
                row = await self._files._require(session, principal, file_id, manage=True, lock=True)
            if row.status == 'purged':
                return await self._files.info(session, row)
            if row.version != expected_version:
                raise AppError('file_version_conflict', '文件已被修改', 409)
            if row.status not in {'deleted', 'purging'}:
                raise AppError('file_not_deleted', '只能清理回收站文件', 409)
            children = list(
                await session.scalars(
                    select(FileRecord)
                    .where(FileRecord.source_file_id == row.id)
                    .order_by(FileRecord.id)
                    .with_for_update()
                )
            )
            ids = [row.id, *(r.id for r in children)]
            refs = await session.scalar(
                select(func.count()).select_from(FileReference).where(FileReference.file_id.in_(ids))
            )
            if refs:
                raise AppError('file_referenced', '文件仍被业务历史引用，不能物理清理', 409)
            keys = [r.storage_key for r in [row, *children] if r.status != 'purged']
            for record in [row, *children]:
                if record.status != 'purged':
                    record.status = 'purging'
            self._files.audit(session, file_id, principal.id, 'purging')
        # Crash here leaves purging. New references reject it; retry safely repeats idempotent deletes.
        for key in keys:
            await self._files.storage.delete(key)
        async with self._transaction() as session:
            final_row = await session.scalar(select(FileRecord).where(FileRecord.id == file_id).with_for_update())
            assert final_row is not None
            row = final_row
            assert row is not None
            children = list(
                await session.scalars(select(FileRecord).where(FileRecord.source_file_id == file_id).with_for_update())
            )
            for record in [row, *children]:
                if record.status == 'purging':
                    record.status = 'purged'
                    record.storage_status = 'missing'
                    self._files.touch(record, acl=True)
            self._files.audit(session, file_id, principal.id, 'purged')
            return await self._files.info(session, row)

    async def run(self, principal: Principal, kind: JobKind) -> JobInfo:
        principal.require_permission('ai:storage:maintain')
        job_id = uuid4()
        async with self._sessions() as guard:
            connection = await guard.connection()
            try:
                # Commit the session nonce before acquiring the session lock: cancellation of acquisition
                # cannot roll the nonce back and leave an unidentifiable advisory lock in the pool.
                await connection.execute(
                    text("SELECT set_config('zhiheng.files_job_attempt',:attempt,false)"), {'attempt': str(job_id)}
                )
                await connection.commit()
                acquired = await connection.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': LOCK_ID})
                await connection.commit()
                if not acquired:
                    raise AppError('file_job_busy', '文件维护任务正在运行', 409)
                # Every read/write transaction uses this very connection. Losing it aborts its transaction;
                # an automatic reconnect loses the session nonce and fails the explicit attempt check.
                sessions = async_sessionmaker(connection, expire_on_commit=False)
                attempt = FileJobs(self._files, sessions, attempt_id=job_id, after_purge=self.after_purge)
                return await attempt._execute_attempt(principal, kind, job_id)
            except BaseException as error:
                code = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'maintenance_failed'
                await asyncio.shield(self._record_failure(job_id, code))
                raise
            finally:
                await self._release_attempt(connection, job_id)

    async def _execute_attempt(self, principal: Principal, kind: JobKind, job_id: UUID) -> JobInfo:
        async with self._transaction() as session:
            await session.execute(
                update(FileJob)
                .where(FileJob.status == 'running')
                .values(status='failed', finished_at=utcnow(), errors=['interrupted'])
            )
            session.add(FileJob(id=job_id, kind=kind))
        scanned, changed, cursor = await self._perform(principal, kind, job_id)
        async with self._transaction() as session:
            job = await session.scalar(
                select(FileJob).where(FileJob.id == job_id, FileJob.status == 'running').with_for_update()
            )
            if job is None:
                raise AppError('file_job_ownership_lost', '文件维护执行权已失效', 409)
            job.scanned = scanned
            job.changed = changed
            job.cursor = cursor
            job.status = 'succeeded'
            job.finished_at = utcnow()
            await session.flush()
            return JobInfo.model_validate(job)

    async def _record_failure(self, job_id: UUID, code: str) -> None:
        # This separate connection may only fail this exact still-running job; it cannot publish success,
        # advance a cursor, or modify files after the guarded execution connection has been lost.
        async with self._sessions.begin() as session:
            await session.execute(
                update(FileJob)
                .where(FileJob.id == job_id, FileJob.status == 'running')
                .values(status='failed', finished_at=utcnow(), errors=[code])
            )

    @staticmethod
    async def _release_attempt(connection: AsyncConnection, job_id: UUID) -> None:
        try:
            if connection.in_transaction():
                await connection.rollback()
            nonce = await connection.scalar(text("SELECT current_setting('zhiheng.files_job_attempt',true)"))
            if nonce == str(job_id):
                await connection.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': LOCK_ID})
                await connection.execute(text("SELECT set_config('zhiheng.files_job_attempt','',false)"))
                await connection.commit()
            else:
                await connection.rollback()
        except Exception:
            # A failed cleanup must neither return a locked physical connection to the pool nor mask
            # the original execution error. Closing it releases any remaining session advisory lock.
            with suppress(Exception):
                await connection.invalidate()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncSession]:
        async with self._sessions.begin() as session:
            if self._attempt_id is not None:
                owned = await session.scalar(
                    text("""
                    SELECT current_setting('zhiheng.files_job_attempt',true)=:attempt
                    AND EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()
                               AND classid=0 AND objid=:key AND objsubid=1 AND granted)
                """),
                    {'attempt': str(self._attempt_id), 'key': LOCK_ID},
                )
                if not owned:
                    raise AppError('file_job_ownership_lost', '文件维护执行权已失效', 409)
            yield session

    async def _perform(self, principal: Principal, kind: JobKind, job_id: UUID) -> tuple[int, int, str]:
        if kind == 'reconcile':
            return await self._reconcile(job_id)
        now = utcnow()
        changed = 0
        async with self._transaction() as session:
            previous = await session.scalar(
                select(FileJob)
                .where(FileJob.kind == kind, FileJob.status == 'succeeded')
                .order_by(FileJob.started_at.desc())
                .limit(1)
            )
            cursor = previous.cursor if previous is not None else ''
            policy = await self._files.policy(session, principal)
            if kind == 'expiry':
                query = select(FileRecord).where(
                    FileRecord.status == 'active',
                    FileRecord.expires_at <= now,
                    FileRecord.source_file_id.is_(None),
                    or_(
                        FileRecord.purpose != 'chat_attachment',
                        ~select(FileReference.id)
                        .where(
                            FileReference.file_id == FileRecord.id,
                            or_(FileReference.retain_until.is_(None), FileReference.retain_until > now),
                        )
                        .exists(),
                    ),
                )
            elif kind == 'reminders':
                query = select(FileRecord).where(
                    FileRecord.status == 'active',
                    FileRecord.expires_at <= now + timedelta(days=policy.reminder_days),
                    FileRecord.source_file_id.is_(None),
                )
            else:
                query = select(FileRecord).where(
                    or_(
                        FileRecord.status == 'purging',
                        (FileRecord.status == 'deleted') & (FileRecord.retention_until <= now),
                    ),
                    FileRecord.source_file_id.is_(None),
                )
            if cursor:
                query = query.where(FileRecord.id > UUID(cursor))
            rows = list(
                await session.scalars(query.order_by(FileRecord.id).limit(BATCH_SIZE).with_for_update(skip_locked=True))
            )
            candidates = [(row.id, row.version, row.owner_id) for row in rows]
            if kind in {'expiry', 'reminders'}:
                for row in rows:
                    if kind == 'expiry':
                        row.status = 'deleted'
                        row.deleted_at = now
                        row.retention_until = now + timedelta(days=policy.retention_days)
                        self._files.touch(row, acl=True)
                        self._files.audit(session, row.id, None, 'expired')
                        changed += 1
                    else:
                        assert row.expires_at is not None
                        key = row.expires_at.isoformat()
                        exists = await session.scalar(
                            select(FileReminder.id).where(
                                FileReminder.file_id == row.id,
                                FileReminder.kind == 'expiry',
                                FileReminder.expiry_key == key,
                            )
                        )
                        if exists is None:
                            session.add(
                                FileReminder(file_id=row.id, owner_id=row.owner_id, kind='expiry', expiry_key=key)
                            )
                            changed += 1
        if kind == 'purge':
            for file_id, version, _ in candidates:
                changed += await self._purge_candidate(principal, file_id, version)
        next_cursor = str(candidates[-1][0]) if len(candidates) == BATCH_SIZE else ''
        return len(candidates), changed, next_cursor

    async def _purge_candidate(self, principal: Principal, file_id: UUID, version: int) -> int:
        try:
            await self.purge(principal, file_id, version)
        except AppError as error:
            if error.code != 'file_referenced':
                raise
            return 0
        return 1

    async def _issue(
        self, session: AsyncSession, job_id: UUID, *, key: str, kind: str, file_id: UUID | None, size: int | None
    ) -> None:
        identity = f'{kind}:{file_id or key}'
        issue = await session.scalar(
            select(FileReconcileIssue).where(FileReconcileIssue.identity_key == identity).with_for_update()
        )
        if issue is None:
            session.add(
                FileReconcileIssue(
                    job_id=job_id,
                    file_id=file_id,
                    identity_key=identity,
                    storage_key=key,
                    kind=kind,
                    size=size,
                    detail=kind,
                    expected_size=size if file_id else None,
                    actual_size=size if not file_id else None,
                )
            )
        elif issue.status == 'resolved':
            issue.status = 'open'
            issue.version += 1
            issue.updated_at = utcnow()
            issue.job_id = job_id

    async def _reconcile(self, job_id: UUID) -> tuple[int, int, str]:
        async with self._transaction() as session:
            previous = await session.scalar(
                select(FileJob)
                .where(FileJob.kind == 'reconcile', FileJob.status == 'succeeded')
                .order_by(FileJob.started_at.desc())
                .limit(1)
            )
            cursor = previous.cursor if previous else ''
            file_cursor, _, object_cursor = cursor.partition(':')
            query = select(FileRecord).where(FileRecord.status != 'purging')
            if file_cursor:
                query = query.where(FileRecord.id > UUID(file_cursor))
            rows = list(await session.scalars(query.order_by(FileRecord.id).limit(BATCH_SIZE)))
            candidates = [(r.id, r.storage_key, r.size, r.sha256, r.status) for r in rows]
        changed = 0
        for file_id, key, size, sha, observed_status in candidates:
            changed += await self._reconcile_record(job_id, file_id, key, size, sha, observed_status)
        entries = await self._files.storage.entries(after=object_cursor, limit=BATCH_SIZE)
        for entry in entries:
            if entry.modified_at > utcnow() - timedelta(hours=1):
                continue
            async with self._transaction() as session:
                exists = await session.scalar(select(FileRecord.id).where(FileRecord.storage_key == entry.key))
                if exists is None:
                    try:
                        content = await self._files.storage.read(entry.key)
                    except AppError:
                        continue
                    await self._issue(session, job_id, key=entry.key, kind='orphan', file_id=None, size=len(content))
                    changed += 1
        next_file = str(candidates[-1][0]) if len(candidates) == BATCH_SIZE else ''
        next_object = entries[-1].key if len(entries) == BATCH_SIZE else ''
        return len(candidates) + len(entries), changed, f'{next_file}:{next_object}'

    async def _reconcile_record(
        self, job_id: UUID, file_id: UUID, key: str, size: int, sha: str, observed_status: str
    ) -> int:
        if observed_status == 'purged':
            # Existing bounded reconciliation repairs post-commit accounting interruptions.
            if self.after_purge is not None:
                await self.after_purge(file_id)
            return 0
        kind = None
        try:
            content = await self._files.storage.read(key)
            if len(content) != size or hashlib.sha256(content).hexdigest() != sha:
                kind = 'size_or_hash'
        except AppError as error:
            kind = 'invalidkey' if error.code == 'invalid_storage_key' else 'missing'
        async with self._transaction() as session:
            row = await session.scalar(select(FileRecord).where(FileRecord.id == file_id).with_for_update())
            if row is None or row.status in {'purging', 'purged'}:
                return 0
            if kind is not None:
                await self._issue(session, job_id, key=key, kind=kind, file_id=file_id, size=size)
                if row.storage_status != 'quarantined':
                    row.storage_status = 'missing' if kind == 'missing' else 'invalid'
                return 1
            if row.storage_status != 'quarantined':
                row.storage_status = 'normal'
        return 0

    async def issue_action(self, principal: Principal, issue_id: UUID, body: IssueAction) -> ReconcileIssueInfo:
        principal.require_permission('ai:storage:maintain')
        async with self._transaction() as session:
            await self._lock_issue_maintenance(session)
            issue = await session.scalar(
                select(FileReconcileIssue).where(FileReconcileIssue.id == issue_id).with_for_update()
            )
            if issue is None:
                raise AppError('file_issue_not_found', '对账问题不存在', 404)
            if issue.version != body.expected_version or (
                issue.status != 'open' and not (issue.status == 'quarantined' and body.action == 'repair')
            ):
                raise AppError('file_version_conflict', '对账问题已被处理', 409)
            await self._require_issue_owner(session, issue, body)
            if body.action == 'ignore':
                issue.status = 'ignored'
            elif body.action == 'quarantine' and issue.kind == 'orphan':
                # Object must still be orphaned; client cannot select a storage path/key.
                if (
                    await session.scalar(select(FileRecord.id).where(FileRecord.storage_key == issue.storage_key))
                    is not None
                ):
                    raise AppError('file_issue_changed', '对象已被登记', 409)
                content = await self._files.storage.read(issue.storage_key)
                info = await self._files.create(
                    session,
                    principal,
                    filename=body.original_filename or 'recovered.bin',
                    content=content,
                    storage_key=issue.storage_key,
                )
                issue.file_id = info.id
                row = await session.get(FileRecord, info.id)
                assert row is not None
                row.storage_status = 'quarantined'
                issue.status = 'quarantined'
                self._files.audit(session, row.id, principal.id, 'reconcile_' + body.action, {'reason': body.reason})
            elif issue.file_id is not None and body.action in {'repair', 'quarantine'}:
                row = await self._maintenance_record(session, principal, issue.file_id)
                if body.action == 'quarantine':
                    row.storage_status = 'quarantined'
                    issue.status = 'quarantined'
                else:
                    content = await self._files.storage.read(row.storage_key)
                    if len(content) != row.size or hashlib.sha256(content).hexdigest() != row.sha256:
                        raise AppError('file_integrity_error', '内容尚未恢复，不能确认修复', 409)
                    row.storage_status = 'normal'
                    issue.status = 'resolved'
                self._files.touch(row, acl=True)
                self._files.audit(session, row.id, principal.id, 'reconcile_' + body.action, {'reason': body.reason})
            else:
                raise AppError('invalid_issue_action', '此问题不支持该操作', 422)
            issue.version += 1
            issue.updated_at = utcnow()
            await session.flush()
            return ReconcileIssueInfo.model_validate(issue)

    @staticmethod
    async def _require_issue_owner(session: AsyncSession, issue: FileReconcileIssue, body: IssueAction) -> None:
        unconfirmed = issue.kind == 'orphan' and body.action in {'register', 'repair'}
        if not unconfirmed and body.action == 'repair' and issue.file_id is not None:
            unconfirmed = (
                await session.scalar(
                    select(FileReconcileIssue.id)
                    .where(
                        FileReconcileIssue.file_id == issue.file_id,
                        FileReconcileIssue.kind == 'orphan',
                        FileReconcileIssue.status == 'quarantined',
                    )
                    .limit(1)
                )
            ) is not None
        if unconfirmed:
            raise AppError('orphan_owner_unconfirmed', '孤立对象尚未确认原所有者，目前仅支持隔离', 409)

    @staticmethod
    async def _maintenance_source(session: AsyncSession, file_id: UUID) -> FileRecord:
        row = await session.scalar(
            select(FileRecord)
            .where(FileRecord.id == file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.source_file_id is not None:
            raise AppError('file_not_found', '文件不存在', 404)
        return row

    async def _maintenance_record(self, session: AsyncSession, principal: Principal, file_id: UUID) -> FileRecord:
        principal.require_permission('ai:storage:maintain')
        record = await session.get(FileRecord, file_id)
        if record is None:
            raise AppError('file_not_found', '文件不存在', 404)
        if record.source_file_id is None:
            source_record = await self._maintenance_source(session, file_id)
            if source_record.status in {'purging', 'purged'}:
                raise AppError('file_not_mutable', '文件正在清理或已清理', 409)
            return source_record
        # Maintenance inherits current source management authority and follows purge's source→artifact lock order.
        source = await self._maintenance_source(session, record.source_file_id)
        if source.status in {'purging', 'purged'}:
            raise AppError('file_not_mutable', '原文件正在清理或已清理', 409)
        artifact = await session.scalar(
            select(FileRecord)
            .where(FileRecord.id == file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if artifact is None or artifact.status in {'purging', 'purged'}:
            raise AppError('file_not_mutable', '派生产物正在清理或已清理', 409)
        return artifact

    @staticmethod
    async def _lock_issue_maintenance(session: AsyncSession) -> None:
        # Reconcile locks file then issue; action must never lock issue then wait on the same running scan.
        if not await session.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': LOCK_ID}):
            raise AppError('file_job_busy', '文件维护任务正在运行', 409)
