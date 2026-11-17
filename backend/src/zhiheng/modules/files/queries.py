"""Native metadata tickets and SQL pagination for explicitly delegated resources."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Select, Uuid, and_, any_, bindparam, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from zhiheng.modules.files.contracts import (
    AuditInfo,
    FileAuditPage,
    FileAuditQuery,
    FileInfo,
    FilePage,
    FileQuery,
    FileStats,
    JobInfo,
    ReconcileIssueInfo,
)
from zhiheng.modules.files.orm import (
    FileAudit,
    FileGrant,
    FileJob,
    FileReconcileIssue,
    FileRecord,
)
from zhiheng.modules.identity.public import IdentityInfo, IdentityService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from zhiheng.modules.files.service import FileService


@dataclass(frozen=True)
class FileReadTicket:
    info: FileInfo
    storage_key: str


@dataclass(frozen=True)
class FileBinding:
    id: UUID
    owner_id: UUID
    ownership_stamp: str


async def resource_page(
    files: 'FileService',
    session: AsyncSession,
    principal: Principal,
    bindings: tuple[FileBinding, ...],
    *,
    purpose: str | None,
    after: tuple[datetime, UUID] | None,
    limit: int,
) -> list[FileInfo]:
    if not bindings:
        return []
    now = utcnow()
    transfers = (
        select(func.count())
        .select_from(FileAudit)
        .where(
            FileAudit.file_id == func.coalesce(FileRecord.source_file_id, FileRecord.id),
            FileAudit.action == 'transferred',
        )
        .correlate(FileRecord)
        .scalar_subquery()
    )
    binding = or_(
        *(
            and_(FileRecord.id == fact.id, FileRecord.owner_id == fact.owner_id, transfers == int(fact.ownership_stamp))
            for fact in bindings
        )
    )
    explicit_allow = (
        select(FileGrant.file_id)
        .where(
            FileGrant.file_id == FileRecord.id,
            FileGrant.user_id == principal.id,
            FileGrant.effect == 'allow',
            or_(FileGrant.expires_at.is_(None), FileGrant.expires_at > now),
        )
        .exists()
    )
    # Artifacts inherit their current source's real ACL and transfer marker. Only explicitly
    # bound artifact IDs are selected; a source grant never enumerates its unbound children.
    source = aliased(FileRecord)
    source_grant = and_(
        FileGrant.file_id == source.id,
        FileGrant.user_id == principal.id,
        or_(FileGrant.expires_at.is_(None), FileGrant.expires_at > now),
    )
    source_allow = select(FileGrant.file_id).where(source_grant, FileGrant.effect == 'allow').correlate(source).exists()
    source_deny = select(FileGrant.file_id).where(source_grant, FileGrant.effect == 'deny').correlate(source).exists()
    readable_source = (
        select(source.id)
        .where(
            source.id == FileRecord.source_file_id,
            source.status == 'active',
            or_(source.expires_at.is_(None), source.expires_at > now),
            source.storage_status.not_in(['missing', 'quarantined', 'invalid']),
            or_(
                source.owner_id == principal.id,
                and_(
                    ~source_deny,
                    or_(
                        source.access == 'public',
                        source_allow,
                        and_(source.uploader_id == principal.id, source.uploader_access_enabled.is_(True)),
                    ),
                ),
            ),
        )
        .correlate(FileRecord)
        .exists()
    )
    original = and_(
        FileRecord.source_file_id.is_(None),
        or_(
            FileRecord.owner_id == principal.id,
            and_(
                explicit_allow,
                or_(FileRecord.expires_at.is_(None), FileRecord.expires_at > now),
                FileRecord.storage_status.not_in(['missing', 'quarantined', 'invalid']),
            ),
        ),
    )
    artifact = and_(
        FileRecord.source_file_id.is_not(None),
        readable_source,
        or_(FileRecord.owner_id == principal.id, explicit_allow),
    )
    query = select(FileRecord).where(binding, FileRecord.status == 'active', or_(original, artifact))
    if purpose is not None:
        query = query.where(FileRecord.purpose == purpose)
    if after is not None:
        query = query.where(tuple_(FileRecord.created_at, FileRecord.id) < after)
    rows = await session.scalars(query.order_by(FileRecord.created_at.desc(), FileRecord.id.desc()).limit(limit))
    return [await files.info(session, row) for row in rows]


class FileCatalog:
    def __init__(self, files: 'FileService', identity: IdentityService) -> None:
        self.files, self._identity = files, identity

    async def delegable_metadata(self, session: AsyncSession, principal: Principal) -> list[FileInfo]:
        now = utcnow()
        shared = select(FileGrant.file_id).where(
            FileGrant.user_id == principal.id,
            FileGrant.effect == 'allow',
            or_(FileGrant.expires_at.is_(None), FileGrant.expires_at > now),
        )
        ids = await session.scalars(
            select(FileRecord.id)
            .where(
                FileRecord.status == 'active',
                or_(FileRecord.owner_id == principal.id, FileRecord.id.in_(shared)),
            )
            .order_by(FileRecord.created_at.desc(), FileRecord.id)
        )
        result = []
        for resource_id in ids:
            try:
                info, _ = await self.files.delegable_access(session, principal, resource_id, active=True)
                result.append(info)
            except AppError as error:  # noqa: PERF203 -- each current ACL must be checked before exposing metadata
                if error.status_code != HTTPStatus.NOT_FOUND:
                    raise
        return result

    async def _filtered(
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
        return self._detail_filters(query, filters, now)

    @staticmethod
    def _detail_filters(
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
        query = await self._filtered(session, principal, filters)
        total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = await session.scalars(
            query.order_by(FileRecord.created_at.desc(), FileRecord.id)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        )
        return FilePage(
            items=[await self.files.info(session, row) for row in rows],
            total=total,
            page=filters.page,
            page_size=filters.page_size,
        )

    async def stats(self, session: AsyncSession, principal: Principal, filters: FileQuery) -> FileStats:
        scoped = (await self._filtered(session, principal, filters)).subquery()
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

    async def audits(self, session: AsyncSession, principal: Principal, file_id: UUID) -> list[AuditInfo]:
        await self.files._require(session, principal, file_id, manage=True)
        return [
            AuditInfo.model_validate(r)
            for r in await session.scalars(
                select(FileAudit).where(FileAudit.file_id == file_id).order_by(FileAudit.created_at.desc()).limit(100)
            )
        ]

    async def users(self, session: AsyncSession, principal: Principal, *, search: str = '') -> list[IdentityInfo]:
        principal.require_permission('ai:storage:read')
        principal.require_permission('system:users:read')
        return await self._identity.directory(session, search=search)

    async def jobs(self, session: AsyncSession, principal: Principal) -> list[JobInfo]:
        principal.require_permission('ai:storage:read')
        return [
            JobInfo.model_validate(r)
            for r in await session.scalars(select(FileJob).order_by(FileJob.started_at.desc()).limit(50))
        ]

    async def issues(
        self, session: AsyncSession, principal: Principal, *, status: str = 'open'
    ) -> list[ReconcileIssueInfo]:
        principal.require_permission('ai:storage:read')
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
        principal.require_permission('ai:storage:read')
        rows = await session.execute(
            select(FileReconcileIssue.status, func.count()).group_by(FileReconcileIssue.status)
        )
        return {
            **dict.fromkeys(['open', 'resolved', 'ignored', 'quarantined'], 0),
            **dict(rows.tuples().all()),
        }

    async def audit_page(
        self, session: AsyncSession, principal: Principal, file_id: UUID, filters: FileAuditQuery
    ) -> FileAuditPage:
        await self.files._require(session, principal, file_id, manage=True)
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
