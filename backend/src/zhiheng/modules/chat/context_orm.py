"""Effective chat heads, summary child facts and frozen per-Run inputs."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class ContextState(Base):
    __tablename__ = 'chat_context_states'
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('chat_conversations.id'), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    pending_task_id: Mapped[UUID | None] = mapped_column(Uuid)
    error_code: Mapped[str | None] = mapped_column(String(64))
    agent_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    agent_source_hash: Mapped[str | None] = mapped_column(String(64))
    checkpoint_cleanup_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SummaryTask(Base):
    __tablename__ = 'chat_summary_tasks'
    __table_args__ = (
        CheckConstraint(
            '(parent_run_id IS NULL) = (parent_attempt_id IS NULL)', name='ck_chat_summary_parent_identity'
        ),
        CheckConstraint("status <> 'pending' OR parent_run_id IS NOT NULL", name='ck_chat_summary_pending_parent'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('chat_conversations.id'), index=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    parent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey('runtime_runs.id'), index=True)
    parent_attempt_id: Mapped[UUID | None] = mapped_column(Uuid)
    expected_version: Mapped[int] = mapped_column(Integer)
    covered_count: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(String(64))
    source_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    file_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey('runtime_runs.id'), unique=True)
    model_binding: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default='pending')
    summary: Mapped[str] = mapped_column(Text, default='', server_default='')
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContextSnapshot(Base):
    __tablename__ = 'chat_context_snapshots'
    __table_args__ = (Index('ix_chat_context_snapshots_checkpoint', 'conversation_id', 'agent_checkpoint_id'),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('chat_conversations.id'), index=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    source_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    file_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    agent_context: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    agent_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
