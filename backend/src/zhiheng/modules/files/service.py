import hashlib
import re
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import DateTime, Select, Uuid, and_, any_, bindparam, delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.files.contracts import (
    AclEntry,
    AclInfo,
    AclRequest,
    AuditInfo,
    BuiltinPermission,
    FileAccess,
    FileAuditPage,
    FileAuditQuery,
    FileInfo,
    FilePage,
    FilePatch,
    FileQuery,
    FileStats,
    FileStatus,
    JobInfo,
    PolicyInfo,
    PolicyRequest,
    PreparedAttachment,
    ReconcileIssueInfo,
    ReferenceInfo,
    ReminderAction,
    ReminderInfo,
    RetentionPolicyInfo,
    RetentionPolicyRequest,
    SourceSnapshot,
    TransferRequest,
)
from zhiheng.modules.files.orm import (
    FileAudit,
    FileGrant,
    FileJob,
    FilePolicy,
    FileReconcileIssue,
    FileRecord,
    FileReference,
    FileReminder,
    FileRetentionPolicy,
)
from zhiheng.modules.identity.public import IdentityService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.storage import StoragePort
from zhiheng.shared.time import utcnow

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILENAME_LENGTH = 255
FIRST_PRINTABLE = 32
DELETE_CONTROL = 127
IMAGE_MIMES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}


def validate_filename(filename: str) -> str:
    if (
        not filename.strip()
        or len(filename) > MAX_FILENAME_LENGTH
        or filename in {'.', '..'}
        or any(ord(char) < FIRST_PRINTABLE or ord(char) == DELETE_CONTROL for char in filename)
        or any(char in filename for char in ('/', '\\', '\x00'))
        or re.search(r'%[0-9a-fA-F]{2}', filename)
    ):
        raise AppError('invalid_file_name', '文件名无效', 422)
    return filename


def sniff_mime(content: bytes, filename: str) -> str:
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if content.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if content.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if content.startswith(b'RIFF') and content[8:12] == b'WEBP':
        return 'image/webp'
    if content.startswith(b'%PDF-'):
        return 'application/pdf'
    extension = filename.rpartition('.')[2].lower()
    if content.startswith(b'PK\x03\x04'):
        return {
            'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        }.get(extension, 'application/zip')
    if b'\x00' not in content[:8192]:
        try:
            content.decode('utf-8-sig')
            return {
                'json': 'application/json',
                'csv': 'text/csv',
                'html': 'text/html',
                'htm': 'text/html',
                'md': 'text/markdown',
                'svg': 'image/svg+xml',
            }.get(extension, 'text/plain')
        except UnicodeError:
            pass
    return 'application/octet-stream'


class FileService:
    def __init__(self, storage: StoragePort, identity: IdentityService) -> None:
        self.storage, self._identity = storage, identity

    async def create_owned(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        owner_id: UUID,
        filename: str,
        content: bytes,
        storage_key: str,
    ) -> FileInfo:
        if owner_id != actor.id:
            actor.require_admin()
        await self._identity.principal_for_user(session, owner_id)
        info = await self.create(session, actor, filename=filename, content=content, storage_key=storage_key)
        row = await session.get(FileRecord, info.id)
        assert row is not None
        row.owner_id = owner_id
        row.uploader_access_enabled = actor.id == owner_id
        return await self.info(session, row)

    async def source_snapshot(
        self,
        session: AsyncSession,
        principal: Principal,
        file_id: UUID,
        *,
        lock: bool = False,
        artifact_file_id: UUID | None = None,
    ) -> SourceSnapshot:
        row = await self._require(session, principal, file_id, active=True, lock=lock)
        if row.source_file_id is not None:
            raise AppError('invalid_source', '请使用原始受管文件', 422)
        if artifact_file_id is not None:
            artifact = await self._require(session, principal, artifact_file_id, active=True)
            if artifact.source_file_id != file_id:
                raise AppError('invalid_source', '派生产物不属于来源文件', 422)
        return SourceSnapshot(
            file_id=row.id,
            version=row.version,
            acl_version=row.acl_version,
            sha256=row.sha256,
            artifact_file_id=artifact_file_id,
        )

    async def bind_sources(
        self,
        session: AsyncSession,
        principal: Principal,
        sources: tuple[SourceSnapshot, ...],
        *,
        resource_type: str,
        resource_id: UUID,
    ) -> None:
        for source in sorted(sources, key=lambda item: item.file_id):
            current = await self.source_snapshot(
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

    async def create(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        filename: str,
        content: bytes,
        storage_key: str,
        source_file_id: UUID | None = None,
        parser_version: str | None = None,
    ) -> FileInfo:
        validate_filename(filename)
        if not content or len(content) > MAX_FILE_BYTES:
            raise AppError('invalid_file_size', '文件为空或超过20MiB', 422)
        row = FileRecord(
            storage_key=storage_key,
            original_filename=filename,
            display_name=filename,
            size=len(content),
            mime=sniff_mime(content, filename),
            sha256=hashlib.sha256(content).hexdigest(),
            owner_id=principal.id,
            uploader_id=principal.id,
            source_file_id=source_file_id,
            parser_version=parser_version,
            storage_status='normal',
        )
        session.add(row)
        await session.flush()
        self.audit(session, row.id, principal.id, 'uploaded')
        return await self.info(session, row)

    @staticmethod
    def audit(
        session: AsyncSession,
        file_id: UUID,
        actor_id: UUID | None,
        action: str,
        details: dict[str, object] | None = None,
    ) -> None:
        session.add(
            FileAudit(
                file_id=file_id, actor_id=actor_id, action=action, details={'outcome': 'completed', **(details or {})}
            )
        )

    @staticmethod
    def managed(principal: Principal, row: FileRecord) -> bool:
        return principal.is_admin or row.owner_id == principal.id

    async def _require(
        self,
        session: AsyncSession,
        principal: Principal,
        file_id: UUID,
        *,
        manage: bool = False,
        active: bool = False,
        lock: bool = False,
    ) -> FileRecord:
        query = select(FileRecord).where(FileRecord.id == file_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await session.scalar(query)
        if row is None:
            raise AppError('file_not_found', '文件不存在', 404)
        if manage:
            if row.source_file_id is not None:
                raise AppError('derived_file_immutable', '派生产物由原文件生命周期管理', 409)
            allowed = self.managed(principal, row)
        else:
            allowed = self.managed(principal, row)
            if not allowed and row.status == 'active' and (row.expires_at is None or row.expires_at > utcnow()):
                grant = await session.get(FileGrant, (row.id, principal.id))
                effective = grant is not None and (grant.expires_at is None or grant.expires_at > utcnow())
                allowed = not (effective and grant is not None and grant.effect == 'deny') and (
                    row.access == 'public'
                    or (row.uploader_access_enabled and row.uploader_id == principal.id)
                    or (effective and grant is not None and grant.effect == 'allow')
                )

        if row.source_file_id is not None and not manage:
            await self._require(session, principal, row.source_file_id, active=True)
            allowed = True
        if not allowed or (
            active
            and (
                row.status != 'active'
                or row.storage_status in {'missing', 'quarantined', 'invalid'}
                or (row.expires_at is not None and row.expires_at <= utcnow())
            )
        ):
            raise AppError('file_not_found', '文件不存在', 404)
        return row

    async def require(
        self, session: AsyncSession, principal: Principal, file_id: UUID, *, active: bool = False, lock: bool = False
    ) -> FileInfo:
        return await self.info(session, await self._require(session, principal, file_id, active=active, lock=lock))

    async def info(self, session: AsyncSession, row: FileRecord) -> FileInfo:
        count = await session.scalar(
            select(func.count()).select_from(FileReference).where(FileReference.file_id == row.id)
        )
        grants = list(await session.scalars(select(FileGrant).where(FileGrant.file_id == row.id)))
        expiries = [g.expires_at for g in grants if g.expires_at is not None and g.expires_at > utcnow()]
        return FileInfo(
            storage_status=cast("Literal['normal','missing','quarantined','invalid','unknown']", row.storage_status),
            acl_entry_count=len(grants),
            acl_nearest_expire_at=min(expiries) if expiries else None,
            id=row.id,
            original_filename=row.original_filename,
            display_name=row.display_name,
            size=row.size,
            mime=row.mime,
            sha256=row.sha256,
            owner_id=row.owner_id,
            uploader_id=row.uploader_id,
            access=cast('FileAccess', row.access),
            uploader_access_enabled=row.uploader_access_enabled,
            status=cast('FileStatus', row.status),
            acl_version=row.acl_version,
            version=row.version,
            expires_at=row.expires_at,
            deleted_at=row.deleted_at,
            retention_until=row.retention_until,
            created_at=row.created_at,
            updated_at=row.updated_at,
            reference_count=count or 0,
            source_file_id=row.source_file_id,
            parser_version=row.parser_version,
        )

    async def get(self, session: AsyncSession, principal: Principal, file_id: UUID) -> FileInfo:
        return await self.info(session, await self._require(session, principal, file_id))

    async def read(self, session: AsyncSession, principal: Principal, file_id: UUID) -> tuple[FileInfo, bytes]:
        row = await self._require(session, principal, file_id, active=True)
        info = await self.info(session, row)
        content = await self.storage.read(row.storage_key)
        if len(content) != row.size or hashlib.sha256(content).hexdigest() != row.sha256:
            raise AppError('file_integrity_error', '文件完整性校验失败', 503)
        return info, content

    async def filtered(
        self, session: AsyncSession, principal: Principal, filters: FileQuery
    ) -> Select[tuple[FileRecord]]:
        now = utcnow()
        valid_grant = and_(
            FileGrant.file_id == FileRecord.id,
            FileGrant.user_id == principal.id,
            or_(FileGrant.expires_at.is_(None), FileGrant.expires_at > now),
        )
        allow = select(FileGrant.file_id).where(valid_grant, FileGrant.effect == 'allow').exists()
        deny = select(FileGrant.file_id).where(valid_grant, FileGrant.effect == 'deny').exists()
        readable = or_(
            FileRecord.owner_id == principal.id,
            and_(
                FileRecord.status == 'active',
                or_(FileRecord.expires_at.is_(None), FileRecord.expires_at > now),
                FileRecord.storage_status.not_in(['missing', 'quarantined', 'invalid']),
                ~deny,
                or_(
                    FileRecord.access == 'public',
                    and_(FileRecord.uploader_id == principal.id, FileRecord.uploader_access_enabled.is_(True)),
                    allow,
                ),
            ),
        )
        query = select(FileRecord).where(FileRecord.source_file_id.is_(None))
        if not principal.is_admin:
            query = query.where(readable)
        if filters.status != 'all':
            query = query.where(FileRecord.status == filters.status)
        if filters.access:
            query = query.where(FileRecord.access == filters.access)
        if filters.owner_id:
            query = query.where(FileRecord.owner_id == filters.owner_id)
        for name, column in (
            (filters.owner_search, FileRecord.owner_id),
            (filters.uploader_search, FileRecord.uploader_id),
        ):
            if name:
                ids = await self._identity.matching_user_ids(session, search=name)
                query = query.where(column == any_(bindparam(column.key + '_matches', list(ids), type_=ARRAY(Uuid()))))
        if filters.search:
            escaped = filters.search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            ids = await self._identity.matching_user_ids(session, search=filters.search)
            matched = any_(bindparam('search_user_matches', list(ids), type_=ARRAY(Uuid())))
            query = query.where(
                or_(
                    FileRecord.display_name.ilike(f'%{escaped}%', escape='\\'),
                    FileRecord.original_filename.ilike(f'%{escaped}%', escape='\\'),
                    FileRecord.owner_id == matched,
                    FileRecord.uploader_id == matched,
                )
            )
        return self.detail_filters(query, filters, now)

    @staticmethod
    def detail_filters(
        query: Select[tuple[FileRecord]], filters: FileQuery, now: datetime
    ) -> Select[tuple[FileRecord]]:
        if filters.mime:
            query = query.where(FileRecord.mime == filters.mime)
        if filters.created_from:
            query = query.where(FileRecord.created_at >= filters.created_from)
        if filters.created_to:
            query = query.where(FileRecord.created_at <= filters.created_to)
        expiration = filters.expiration_status
        if expiration == 'permanent':
            query = query.where(FileRecord.expires_at.is_(None))
        elif expiration == 'valid':
            query = query.where(FileRecord.expires_at > now + timedelta(days=7))
        elif expiration == 'expiring':
            query = query.where(FileRecord.expires_at > now, FileRecord.expires_at <= now + timedelta(days=7))
        elif expiration == 'expired' or filters.expired is True:
            query = query.where(FileRecord.expires_at <= now)
        elif filters.expired is False:
            query = query.where(or_(FileRecord.expires_at.is_(None), FileRecord.expires_at > now))
        return query

    async def list_files(self, session: AsyncSession, principal: Principal, filters: FileQuery) -> FilePage:
        query = await self.filtered(session, principal, filters)
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = await session.scalars(
            query.order_by(FileRecord.created_at.desc(), FileRecord.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
        return FilePage(
            items=[await self.info(session, row) for row in rows],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    async def stats(self, session: AsyncSession, principal: Principal, filters: FileQuery) -> FileStats:
        scoped = (await self.filtered(session, principal, filters)).subquery()
        c = scoped.c
        now = utcnow()
        row = (
            await session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(c.size).filter(c.status != 'purged'), 0),
                    func.count().filter(c.status == 'active'),
                    func.count().filter(c.status == 'deleted'),
                    func.count().filter(c.access == 'public', c.status == 'active'),
                    func.coalesce(func.sum(c.size).filter(c.access == 'public', c.status != 'purged'), 0),
                    func.coalesce(func.sum(c.size).filter(c.access == 'private', c.status != 'purged'), 0),
                    func.count().filter(c.expires_at <= now),
                    func.count().filter(c.retention_until <= now + timedelta(days=7)),
                )
            )
        ).one()
        acl = (
            await session.scalar(
                select(func.count())
                .select_from(FileGrant)
                .where(
                    FileGrant.file_id.in_(select(c.id)),
                    FileGrant.expires_at > now,
                    FileGrant.expires_at <= now + timedelta(days=7),
                )
            )
            or 0
        )
        return FileStats(
            total_count=row[0],
            total_bytes=row[1],
            active_count=row[2],
            deleted_count=row[3],
            public_count=row[4],
            public_bytes=row[5],
            private_bytes=row[6],
            expired_count=row[7],
            retention_expiring_count=row[8],
            acl_expiring_count=acl,
        )

    @staticmethod
    def check_version(row: FileRecord, expected: int, *, acl: bool = False) -> None:
        if (row.acl_version if acl else row.version) != expected:
            raise AppError('file_version_conflict', '文件已被修改，请刷新后重试', 409)
        if row.status in {'purging', 'purged'}:
            raise AppError('file_not_mutable', '文件正在清理或已清理', 409)

    @staticmethod
    def touch(row: FileRecord, *, acl: bool = False) -> None:
        row.version += 1
        if acl:
            row.acl_version += 1
        row.updated_at = utcnow()

    async def patch(self, session: AsyncSession, principal: Principal, file_id: UUID, body: FilePatch) -> FileInfo:
        row = await self._require(session, principal, file_id, manage=True, lock=True)
        self.check_version(row, body.expected_version)
        if body.display_name is not None:
            row.display_name = validate_filename(body.display_name)
        if 'expires_at' in body.model_fields_set:
            if body.expires_at is not None and body.expires_at.utcoffset() is None:
                raise AppError('invalid_expiry', '到期时间必须包含时区', 422)
            row.expires_at = body.expires_at
        if body.uploader_access_enabled is not None:
            row.uploader_access_enabled = body.uploader_access_enabled
        self.touch(row, acl=True)
        self.audit(session, row.id, principal.id, 'updated')
        return await self.info(session, row)

    async def acl(self, session: AsyncSession, principal: Principal, file_id: UUID) -> AclInfo:
        row = await self._require(session, principal, file_id, manage=True)
        grants = list(
            await session.scalars(select(FileGrant).where(FileGrant.file_id == file_id).order_by(FileGrant.user_id))
        )
        return AclInfo(
            file=await self.info(session, row),
            entries=[
                AclEntry(user_id=g.user_id, effect=cast("Literal['allow','deny']", g.effect), expires_at=g.expires_at)
                for g in grants
            ],
            builtin_permissions=[
                BuiltinPermission(source='admin', enabled=True, deny_overridable=False),
                BuiltinPermission(source='owner', enabled=True, deny_overridable=False),
                BuiltinPermission(source='uploader', enabled=row.uploader_access_enabled, deny_overridable=True),
            ],
        )

    async def set_acl(self, session: AsyncSession, principal: Principal, file_id: UUID, body: AclRequest) -> AclInfo:
        row = await self._require(session, principal, file_id, manage=True, lock=True)
        self.check_version(row, body.expected_version, acl=True)
        entries = body.entries
        if len({g.user_id for g in entries}) != len(entries):
            raise AppError('duplicate_acl_entry', '权限用户重复', 422)
        for entry in entries:
            await self._identity.principal_for_user(session, entry.user_id)
            if entry.expires_at is not None and entry.expires_at.utcoffset() is None:
                raise AppError('invalid_expiry', '权限到期时间必须包含时区', 422)
        await session.execute(delete(FileGrant).where(FileGrant.file_id == file_id))
        session.add_all(
            FileGrant(file_id=file_id, user_id=g.user_id, effect=g.effect, expires_at=g.expires_at) for g in entries
        )
        row.access = body.access
        self.touch(row, acl=True)
        self.audit(session, file_id, principal.id, 'acl_updated')
        await session.flush()
        return await self.acl(session, principal, file_id)

    async def transfer(
        self, session: AsyncSession, principal: Principal, file_id: UUID, body: TransferRequest
    ) -> FileInfo:
        row = await self._require(session, principal, file_id, manage=True, lock=True)
        self.check_version(row, body.expected_version)
        owner = await self._identity.principal_for_user(session, body.owner_id)
        previous = row.owner_id
        row.owner_id = owner.id
        row.uploader_access_enabled = body.uploader_access_enabled
        self.touch(row, acl=True)
        self.audit(
            session,
            file_id,
            principal.id,
            'transferred',
            {'previous_owner_id': str(previous), 'owner_id': str(owner.id), 'reason': body.reason},
        )
        return await self.info(session, row)

    async def remove(
        self,
        session: AsyncSession,
        principal: Principal,
        file_id: UUID,
        expected_version: int,
        *,
        restore: bool = False,
    ) -> FileInfo:
        row = await self._require(session, principal, file_id, manage=True, lock=True)
        self.check_version(row, expected_version)
        if restore and row.status != 'deleted':
            raise AppError('file_not_deleted', '文件不在回收站', 409)
        if not restore:
            if row.status != 'active':
                raise AppError('file_not_active', '只能删除活跃文件', 409)
            ids = await self._lock_family(session, row)
            references = await session.scalar(
                select(func.count()).select_from(FileReference).where(FileReference.file_id.in_(ids))
            )
            if references:
                raise AppError('file_referenced', '文件仍被业务历史引用，不能删除', 409)
        row.status = 'active' if restore else 'deleted'
        row.deleted_at = None if restore else utcnow()
        policy = await self.policy(session)
        row.retention_until = None if restore else utcnow() + timedelta(days=policy.retention_days)
        self.touch(row, acl=True)
        self.audit(session, file_id, principal.id, 'restored' if restore else 'deleted')
        return await self.info(session, row)

    @staticmethod
    async def _lock_family(session: AsyncSession, row: FileRecord) -> list[UUID]:
        # The caller holds the source row. Public reference admission/cleanup use the same source-first order.
        children = await session.scalars(
            select(FileRecord.id).where(FileRecord.source_file_id == row.id).order_by(FileRecord.id).with_for_update()
        )
        return [row.id, *children]

    async def references(self, session: AsyncSession, principal: Principal, file_id: UUID) -> list[ReferenceInfo]:
        await self._require(session, principal, file_id, manage=True)
        return [
            ReferenceInfo.model_validate(r)
            for r in await session.scalars(
                select(FileReference)
                .where(FileReference.file_id == file_id)
                .order_by(FileReference.created_at.desc())
                .limit(100)
            )
        ]

    async def audits(self, session: AsyncSession, principal: Principal, file_id: UUID) -> list[AuditInfo]:
        await self._require(session, principal, file_id, manage=True)
        return [
            AuditInfo.model_validate(r)
            for r in await session.scalars(
                select(FileAudit).where(FileAudit.file_id == file_id).order_by(FileAudit.created_at.desc()).limit(100)
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
        policy = await session.get(FileRetentionPolicy, resource_type)
        retain_until = utcnow() + timedelta(days=policy.retention_days) if policy and policy.enabled else None
        for item in sorted(attachments, key=lambda v: v.summary.file_id):
            row = await self._require(session, principal, item.summary.file_id, active=True, lock=True)
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

    async def policy(self, session: AsyncSession) -> PolicyInfo:
        row = await session.get(FilePolicy, 1)
        return PolicyInfo(
            retention_days=row.retention_days if row else 30,
            reminder_days=row.reminder_days if row else 7,
            version=row.version if row else 1,
        )

    async def set_policy(self, session: AsyncSession, principal: Principal, body: PolicyRequest) -> PolicyInfo:
        principal.require_admin()

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
        return await self.policy(session)

    async def cached_artifact(
        self, session: AsyncSession, principal: Principal, source_id: UUID, parser_version: str
    ) -> tuple[UUID, str] | None:
        await self._require(session, principal, source_id, active=True)
        row = await session.scalar(
            select(FileRecord)
            .where(
                FileRecord.source_file_id == source_id,
                FileRecord.parser_version == parser_version,
                FileRecord.status == 'active',
            )
            .order_by(FileRecord.id)
            .limit(1)
        )
        return (row.id, row.storage_key) if row is not None else None

    async def artifact_content(self, session: AsyncSession, principal: Principal, file_id: UUID) -> bytes:
        _, content = await self.read(session, principal, file_id)
        return content

    async def create_artifact(
        self, session: AsyncSession, *, source_id: UUID, content: bytes, storage_key: str, parser_version: str
    ) -> FileInfo:
        source = await session.get(FileRecord, source_id)
        assert source is not None
        owner = await self._identity.principal_for_user(session, source.owner_id)
        return await self.create(
            session,
            owner,
            filename='parsed.json',
            content=content,
            storage_key=storage_key,
            source_file_id=source_id,
            parser_version=parser_version,
        )

    async def retention_policies(self, session: AsyncSession, principal: Principal) -> list[RetentionPolicyInfo]:
        principal.require_admin()
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
        principal.require_admin()

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
        if not principal.is_admin:
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
        file = await self._require(session, principal, reminder.file_id, manage=True)
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
        info = await self.patch(
            session, principal, file_id, FilePatch(expected_version=body.expected_version, expires_at=body.expires_at)
        )
        self.audit(session, file_id, principal.id, 'expiry_extended', {'reason': body.reason})
        return info

    async def dispose_expired(
        self, session: AsyncSession, principal: Principal, file_id: UUID, expected_version: int
    ) -> FileInfo:
        row = await self._require(session, principal, file_id, manage=True, lock=True)
        self.check_version(row, expected_version)
        if row.status not in {'active', 'deleted'}:
            raise AppError('file_not_mutable', '文件状态不允许到期处置', 409)
        now = utcnow()
        if row.expires_at is None or row.expires_at > now:
            raise AppError('file_not_expired', '文件尚未到期', 409)
        ids = await self._lock_family(session, row)
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
        self.audit(session, file_id, principal.id, 'expired_references_released', {'count': expired})
        if row.status == 'deleted':
            # Expiry sweep already fixed this recycle deadline; disposal must not restart its retention period.
            self.touch(row)
            return await self.info(session, row)
        return await self.remove(session, principal, file_id, expected_version)

    async def jobs(self, session: AsyncSession, principal: Principal) -> list[JobInfo]:
        principal.require_admin()
        return [
            JobInfo.model_validate(r)
            for r in await session.scalars(select(FileJob).order_by(FileJob.started_at.desc()).limit(50))
        ]

    async def issues(
        self, session: AsyncSession, principal: Principal, *, status: str = 'open'
    ) -> list[ReconcileIssueInfo]:
        principal.require_admin()
        return [
            ReconcileIssueInfo.model_validate(r)
            for r in await session.scalars(
                select(FileReconcileIssue)
                .where(FileReconcileIssue.status == status)
                .order_by(FileReconcileIssue.created_at.desc())
                .limit(100)
            )
        ]

    async def issue_stats(self, session: AsyncSession, principal: Principal) -> dict[str, int]:
        principal.require_admin()
        rows = await session.execute(
            select(FileReconcileIssue.status, func.count()).group_by(FileReconcileIssue.status)
        )
        return {
            **dict.fromkeys(['open', 'resolved', 'ignored', 'quarantined'], 0),
            **dict(rows.tuples()),
        }

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
            self.audit(session, row.id, actor_id, 'references_released', {'resource_type': resource_type})

    async def record_access(
        self,
        session: AsyncSession,
        principal: Principal | None,
        file_id: UUID,
        *,
        outcome: Literal['allowed', 'denied'],
        bytes_sent: int,
        range_header: str | None,
        error_code: str | None = None,
    ) -> None:
        row = await session.get(FileRecord, file_id)
        if row is not None:
            self.audit(
                session,
                row.source_file_id or row.id,
                principal.id if principal else None,
                'download',
                {
                    'outcome': outcome,
                    'bytes_sent': bytes_sent,
                    'range': range_header[:120] if range_header else None,
                    'error_code': error_code,
                    'requested_file_id': str(file_id),
                },
            )

    async def audit_page(
        self, session: AsyncSession, principal: Principal, file_id: UUID, filters: FileAuditQuery
    ) -> FileAuditPage:
        await self._require(session, principal, file_id, manage=True)
        query = select(FileAudit).where(FileAudit.file_id == file_id)
        if filters.action:
            query = query.where(FileAudit.action == filters.action)
        if filters.actor_id:
            query = query.where(FileAudit.actor_id == filters.actor_id)
        if filters.outcome:
            query = query.where(FileAudit.details['outcome'].astext == filters.outcome)
        if filters.created_from:
            query = query.where(FileAudit.created_at >= filters.created_from)
        if filters.created_to:
            query = query.where(FileAudit.created_at <= filters.created_to)
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = await session.scalars(
            query.order_by(FileAudit.created_at.desc(), FileAudit.id.desc())
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
        return FileAuditPage(
            items=[AuditInfo.model_validate(row) for row in rows],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )
