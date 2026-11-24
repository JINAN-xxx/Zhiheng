"""File identity, authorization, integrity and explicit domain composition."""

import hashlib
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import Uuid, any_, bindparam, delete, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.files.contracts import (
    AclEntry,
    AclInfo,
    AclRequest,
    BuiltinPermission,
    FileAccess,
    FileAccountingFact,
    FileInfo,
    FilePatch,
    FileStatus,
    SourceSnapshot,
    TransferRequest,
)
from zhiheng.modules.files.lifecycle import FileLifecycle
from zhiheng.modules.files.metadata import sniff_mime, validate_filename
from zhiheng.modules.files.orm import (
    FileAudit,
    FileGrant,
    FileRecord,
    FileReference,
)
from zhiheng.modules.files.queries import FileCatalog, FileReadTicket
from zhiheng.modules.files.references import FileReferences
from zhiheng.modules.identity.public import IdentityService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.storage import StoragePort
from zhiheng.shared.time import utcnow

MAX_FILE_BYTES = 20 * 1024 * 1024
LibraryAccess = Callable[..., Awaitable[bool]]
LibraryLocks = Callable[[AsyncSession, Principal, tuple[UUID, ...]], Awaitable[None]]


class FileService:
    def __init__(self, storage: StoragePort, identity: IdentityService) -> None:
        self.storage, self._identity = storage, identity
        self.queries = FileCatalog(self, identity)
        self.lifecycle = FileLifecycle(self)
        self.references = FileReferences(self)
        self.library_access: LibraryAccess | None = None
        self.library_locks: LibraryLocks | None = None

    async def create_owned(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        owner_id: UUID,
        filename: str,
        content: bytes,
        storage_key: str,
        purpose: str = 'general',
    ) -> FileInfo:
        if owner_id != actor.id:
            actor.require_admin()
        await self._identity.principal_for_user(session, owner_id)
        info = await self.create(
            session, actor, filename=filename, content=content, storage_key=storage_key, purpose=purpose
        )
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

    async def source_root_ids(self, session: AsyncSession, file_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
        roots = set()
        for file_id in sorted(set(file_ids)):
            cursor, visited = file_id, set()
            while True:
                if cursor in visited:
                    raise AppError('invalid_source', '文件来源关系无效', 409)
                visited.add(cursor)
                row = await session.scalar(select(FileRecord).where(FileRecord.id == cursor))
                if row is None:
                    raise AppError('file_not_found', '文件不存在', 404)
                if row.source_file_id is None:
                    roots.add(row.id)
                    break
                cursor = row.source_file_id
        return tuple(sorted(roots))

    async def _lock_libraries(self, session: AsyncSession, principal: Principal, file_ids: tuple[UUID, ...]) -> None:
        if self.library_locks is None or not file_ids:
            return
        # The business module orders KB locks; Files continues to own source/artifact row ordering.
        await self.library_locks(session, principal, await self.source_root_ids(session, file_ids))

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
        purpose: str = 'general',
    ) -> FileInfo:
        validate_filename(filename)
        if not content or len(content) > MAX_FILE_BYTES:
            raise AppError('invalid_file_size', '文件为空或超过20MiB', 422)
        row = FileRecord(
            storage_key=storage_key,
            original_filename=filename,
            display_name=filename,
            purpose=purpose,
            size=len(content),
            mime=sniff_mime(content, filename),
            sha256=hashlib.sha256(content).hexdigest(),
            owner_id=principal.id,
            uploader_id=principal.id,
            source_file_id=source_file_id,
            parser_version=parser_version,
            storage_status='normal',
            expires_at=utcnow() + timedelta(hours=24) if purpose == 'chat_attachment' else None,
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
        return row.owner_id == principal.id

    async def _locked_read(
        self, session: AsyncSession, principal: Principal, file_id: UUID
    ) -> tuple[FileRecord, bool, bool]:
        # Discover immutable provenance without holding a file row. Business ACLs lock their KB first.
        observed = await session.scalar(select(FileRecord).where(FileRecord.id == file_id))
        if observed is None:
            raise AppError('file_not_found', '文件不存在', 404)
        source_id = observed.source_file_id
        if source_id is not None:
            await self._require(session, principal, source_id, active=True, lock=True)
        library = bool(self.library_access and await self.library_access(session, principal, file_id, lock=True))
        row = await session.scalar(
            select(FileRecord)
            .where(FileRecord.id == file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AppError('file_not_found', '文件不存在', 404)
        if row.source_file_id != source_id:
            raise AppError('file_snapshot_changed', '文件来源已变化，请重试', 409)
        return row, library, source_id is not None

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
        source_authorized = False
        if lock and not manage:
            row, library, source_authorized = await self._locked_read(session, principal, file_id)
        else:
            query = select(FileRecord).where(FileRecord.id == file_id)
            if lock:
                query = query.with_for_update().execution_options(populate_existing=True)
            candidate = await session.scalar(query)
            if candidate is None:
                raise AppError('file_not_found', '文件不存在', 404)
            row = candidate
            library = bool(
                not manage and self.library_access and await self.library_access(session, principal, file_id, lock=lock)
            )
        if manage:
            if row.source_file_id is not None:
                await self._require(session, principal, row.source_file_id)
                raise AppError('derived_file_immutable', '派生产物由原文件生命周期管理', 409)
            allowed = self.managed(principal, row)
        else:
            allowed = library or self.managed(principal, row)
            if not allowed and row.status == 'active' and (row.expires_at is None or row.expires_at > utcnow()):
                grant = await session.get(FileGrant, (row.id, principal.id), populate_existing=lock)
                effective = grant is not None and (grant.expires_at is None or grant.expires_at > utcnow())
                allowed = not (effective and grant is not None and grant.effect == 'deny') and (
                    row.access == 'public'
                    or (row.uploader_access_enabled and row.uploader_id == principal.id)
                    or (effective and grant is not None and grant.effect == 'allow')
                )

        if row.source_file_id is not None and not manage:
            if not source_authorized:
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

    async def ensure_readable(self, session: AsyncSession, principal: Principal, file_id: UUID) -> None:
        await self._require(session, principal, file_id, active=True)

    async def active_original_ids(self, session: AsyncSession, file_ids: set[UUID]) -> set[UUID]:
        """Check file lifecycle after the caller has checked the owning resource's ACL."""
        if not file_ids:
            return set()
        ids = bindparam('authorized_file_ids', list(file_ids), type_=ARRAY(Uuid()))
        return set(
            await session.scalars(
                select(FileRecord.id).where(
                    FileRecord.id == any_(ids),
                    FileRecord.source_file_id.is_(None),
                    FileRecord.status == 'active',
                    FileRecord.storage_status.not_in(('missing', 'quarantined', 'invalid')),
                    or_(FileRecord.expires_at.is_(None), FileRecord.expires_at > utcnow()),
                )
            )
        )

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
            purpose=row.purpose,
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

    async def delegable_access(
        self, session: AsyncSession, principal: Principal, file_id: UUID, *, lock: bool = False, active: bool = False
    ) -> tuple[FileInfo, frozenset[str]]:
        """Ownership or explicit current read sharing can be delegated; global admin access cannot."""
        personal = principal.personal()
        row = await self._require(session, personal, file_id, active=active, lock=lock)
        actions = (
            frozenset({'read', 'write', 'delete'})
            if row.owner_id == personal.id and row.source_file_id is None
            else frozenset({'read'})
        )
        if row.owner_id != personal.id:
            grant = await session.get(FileGrant, (row.id, personal.id), populate_existing=lock)
            if (
                grant is None
                or grant.effect != 'allow'
                or (grant.expires_at is not None and grant.expires_at <= utcnow())
            ):
                raise AppError('file_not_found', '文件不存在', 404)
        return await self.info(session, row), actions

    async def ownership_stamp(self, session: AsyncSession, file_id: UUID) -> str:
        """Immutable transfer audit facts invalidate prior delegation even after A -> C -> A."""
        count = await session.scalar(
            select(func.count())
            .select_from(FileAudit)
            .where(FileAudit.file_id == file_id, FileAudit.action == 'transferred')
        )
        return str(count or 0)

    async def accounting_fact(self, session: AsyncSession, file_id: UUID, *, lock: bool = False) -> FileAccountingFact:
        """Trusted lifecycle metadata only; never exposes content or supplies user authorization."""
        query = select(FileRecord).where(FileRecord.id == file_id)
        if lock:
            query = query.with_for_update()
        row = await session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise AppError('file_not_found', '文件不存在', 404)
        return FileAccountingFact(row.id, row.owner_id, row.size, row.status, row.source_file_id)

    async def get(self, session: AsyncSession, principal: Principal, file_id: UUID) -> FileInfo:
        return await self.info(session, await self._require(session, principal, file_id))

    async def object_registered(self, session: AsyncSession, storage_key: str) -> bool:
        """Trusted cleanup fact; no physical key is accepted from resource clients."""
        return await session.scalar(select(FileRecord.id).where(FileRecord.storage_key == storage_key)) is not None

    async def read_ticket(self, session: AsyncSession, principal: Principal, file_id: UUID) -> FileReadTicket:
        row = await self._require(session, principal, file_id, active=True)
        return FileReadTicket(await self.info(session, row), row.storage_key)

    async def read_content(self, ticket: FileReadTicket) -> bytes:
        content = await self.storage.read(ticket.storage_key)
        if len(content) != ticket.info.size or hashlib.sha256(content).hexdigest() != ticket.info.sha256:
            raise AppError('file_integrity_error', '文件完整性校验失败', 503)
        return content

    async def read(self, session: AsyncSession, principal: Principal, file_id: UUID) -> tuple[FileInfo, bytes]:
        ticket = await self.read_ticket(session, principal, file_id)
        return ticket.info, await self.read_content(ticket)

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

    @staticmethod
    async def _lock_family(session: AsyncSession, row: FileRecord) -> list[UUID]:
        # The caller holds the source row. Public reference admission/cleanup use the same source-first order.
        children = await session.scalars(
            select(FileRecord.id).where(FileRecord.source_file_id == row.id).order_by(FileRecord.id).with_for_update()
        )
        return [row.id, *children]

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
        info = await self.create(
            session,
            owner,
            filename='parsed.json',
            content=content,
            storage_key=storage_key,
            source_file_id=source_id,
            parser_version=parser_version,
        )
        artifact = await session.get(FileRecord, info.id)
        assert artifact is not None
        artifact.expires_at = source.expires_at
        return await self.info(session, artifact)

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
