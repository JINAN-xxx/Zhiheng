from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class WorkTask(Base):
    __tablename__ = 'work_tasks'
    __table_args__ = (UniqueConstraint('owner_id', 'create_client_request_id', name='uq_work_create'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    title: Mapped[str] = mapped_column(String(100))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default='active')
    version: Mapped[int] = mapped_column(Integer, default=1)
    current_run_id: Mapped[UUID | None] = mapped_column(ForeignKey('runtime_runs.id'))
    active_agent_id: Mapped[UUID | None] = mapped_column(Uuid)
    create_active_agent_id: Mapped[UUID] = mapped_column(Uuid)
    create_client_request_id: Mapped[UUID] = mapped_column(Uuid)
    create_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkMember(Base):
    __tablename__ = 'work_members'
    __table_args__ = (
        Index('uq_work_active_member', 'task_id', 'agent_id', unique=True, postgresql_where=text('removed_at IS NULL')),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey('work_tasks.id'), index=True)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey('agents.id'))
    agent_owner_id: Mapped[UUID] = mapped_column(Uuid)
    agent_name_snapshot: Mapped[str] = mapped_column(String(100))
    published_version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, object]] = mapped_column(JSONB)
    skills: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkMessage(Base):
    __tablename__ = 'work_messages'
    __table_args__ = (
        UniqueConstraint('task_id', 'sequence', name='uq_work_message_sequence'),
        UniqueConstraint('task_id', 'client_request_id', 'role', name='uq_work_request_role'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey('work_tasks.id'), index=True)
    client_request_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default='')
    reasoning: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(20), default='completed')
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey('runtime_runs.id'), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    audit: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    usage: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    agent_id: Mapped[UUID | None] = mapped_column(Uuid)
    agent_name_snapshot: Mapped[str | None] = mapped_column(String(100))
    published_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
