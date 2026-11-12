"""Durable parsing configuration and fenced per-document attempts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class ParsingConfiguration(Base):
    __tablename__ = 'document_parse_settings'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)


class ParsingJob(Base):
    __tablename__ = 'document_parse_jobs'
    __table_args__ = (UniqueConstraint('parent_id', 'file_id', 'source_sha256'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    parent_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_jobs.id'), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'), index=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    source_version: Mapped[int] = mapped_column(Integer)
    source_acl_version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, object]] = mapped_column(JSONB)
    engine: Mapped[str] = mapped_column(String(16), default='local')
    phase: Mapped[str] = mapped_column(String(24), default='selecting', index=True)
    task_id: Mapped[str | None] = mapped_column(String(256))
    upstream_file_id: Mapped[str | None] = mapped_column(String(256))
    artifact_id: Mapped[UUID | None] = mapped_column(ForeignKey('files.id'))
    engine_version: Mapped[str | None] = mapped_column(String(80))
    parser_version: Mapped[str | None] = mapped_column(String(64))
    token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))
    history: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
