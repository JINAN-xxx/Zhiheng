"""Native metadata tickets and SQL pagination for explicitly delegated resources."""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import and_, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from zhiheng.modules.files.contracts import FileInfo
from zhiheng.modules.files.orm import FileAudit, FileGrant, FileRecord
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
