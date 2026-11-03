"""Durable memory state, summary publication facts and frozen per-Run context facts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class ContextState(Base):
    __tablename__ = 'chat_context_states'
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('chat_conversations.id'), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    version: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    target_covered_count: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    dirty: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true', index=True)
    summary_task_id: Mapped[UUID | None] = mapped_column(Uuid)
    pending_task_id: Mapped[UUID | None] = mapped_column(Uuid)
    last_run_id: Mapped[UUID | None] = mapped_column(ForeignKey('runtime_runs.id'))
    error_code: Mapped[str | None] = mapped_column(String(64))


class SummaryTask(Base):
    __tablename__ = 'chat_summary_tasks'
    __table_args__ = (UniqueConstraint('owner_id', 'retry_request_id', name='uq_chat_summary_retry_request'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('chat_conversations.id'), index=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    expected_version: Mapped[int] = mapped_column(Integer)
    covered_count: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(String(64))
    source_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    file_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    previous_task_id: Mapped[UUID | None] = mapped_column(Uuid)
    retry_request_id: Mapped[UUID | None] = mapped_column(Uuid)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey('runtime_runs.id'), unique=True)
    model_binding: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default='pending')
    summary: Mapped[str] = mapped_column(Text, default='', server_default='')
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContextSnapshot(Base):
    __tablename__ = 'chat_context_snapshots'
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('chat_conversations.id'), index=True)
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    summary_task_id: Mapped[UUID | None] = mapped_column(Uuid)
    summary_version: Mapped[int] = mapped_column(Integer)
    source_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    file_refs: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    estimated_tokens: Mapped[int] = mapped_column(Integer)
    input_budget: Mapped[int] = mapped_column(Integer)
    dropped_turns: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
