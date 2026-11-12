from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class FileRecord(Base):
    __tablename__ = 'files'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    storage_key: Mapped[str] = mapped_column(String(32), unique=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(Integer)
    purpose: Mapped[str] = mapped_column(String(64), default='general', server_default='general')
    mime: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    uploader_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    access: Mapped[str] = mapped_column(String(16), default='private')
    uploader_access_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default='active', index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    acl_version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_file_id: Mapped[UUID | None] = mapped_column(ForeignKey('files.id'), index=True)
    storage_status: Mapped[str] = mapped_column(String(16), default='unknown')
    parser_version: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FileGrant(Base):
    __tablename__ = 'file_grants'
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), primary_key=True)
    effect: Mapped[str] = mapped_column(String(8), default='allow')
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FileReference(Base):
    __tablename__ = 'file_references'
    __table_args__ = (UniqueConstraint('file_id', 'resource_type', 'resource_id'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'), index=True)
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[UUID] = mapped_column(Uuid)
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FileAudit(Base):
    __tablename__ = 'file_audits'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'), index=True)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FilePolicy(Base):
    __tablename__ = 'file_policy'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    reminder_days: Mapped[int] = mapped_column(Integer, default=7)
    version: Mapped[int] = mapped_column(Integer, default=1)


class FileReminder(Base):
    __tablename__ = 'file_reminders'
    __table_args__ = (UniqueConstraint('file_id', 'kind', 'expiry_key'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'))
    owner_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    read_by: Mapped[UUID | None] = mapped_column(Uuid)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FileJob(Base):
    __tablename__ = 'file_jobs'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default='running')
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scanned: Mapped[int] = mapped_column(Integer, default=0)
    changed: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[str]] = mapped_column(JSONB, default=list)
    cursor: Mapped[str] = mapped_column(String(128), default='')


class FileRetentionPolicy(Base):
    __tablename__ = 'file_retention_policies'
    resource_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    retention_days: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str] = mapped_column(String(500), default='')
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FileReconcileIssue(Base):
    __tablename__ = 'file_reconcile_issues'
    __table_args__ = (UniqueConstraint('identity_key'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey('file_jobs.id'))
    file_id: Mapped[UUID | None] = mapped_column(ForeignKey('files.id'))
    identity_key: Mapped[str] = mapped_column(String(100))
    storage_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default='open')
    version: Mapped[int] = mapped_column(Integer, default=1)
    size: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str] = mapped_column(String(500), default='')
    expected_size: Mapped[int | None] = mapped_column(Integer)
    actual_size: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
