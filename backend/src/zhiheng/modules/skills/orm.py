from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class Skill(Base):
    __tablename__ = 'skills'
    __table_args__ = (
        CheckConstraint("status IN ('enabled','disabled')"),
        CheckConstraint("source IN ('builtin','upload')"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(16), default='enabled')
    source: Mapped[str] = mapped_column(String(16), default='upload')
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SkillRevision(Base):
    __tablename__ = 'skill_revisions'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    skill_id: Mapped[UUID] = mapped_column(ForeignKey('skills.id'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(500))
    package_version: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    package_sha: Mapped[str] = mapped_column(String(64))
    package_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    filename: Mapped[str] = mapped_column(String(255))
    file_count: Mapped[int] = mapped_column(Integer)
    total_bytes: Mapped[int] = mapped_column(Integer)


class SkillDraft(Base):
    __tablename__ = 'skill_drafts'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    package_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    package_sha: Mapped[str] = mapped_column(String(64))
    parsed_hash: Mapped[str] = mapped_column(String(64))
    target_skill_id: Mapped[UUID | None] = mapped_column(ForeignKey('skills.id'))
    target_version: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16), default='install', server_default='install')
    confirmed_skill_id: Mapped[UUID | None] = mapped_column(ForeignKey('skills.id'))
    confirmed_revision_id: Mapped[UUID | None] = mapped_column(ForeignKey('skill_revisions.id'))
    confirmed_info: Mapped[dict[str, object] | None] = mapped_column(JSONB)
