from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class Agent(Base):
    __tablename__ = 'agents'
    __table_args__ = (UniqueConstraint('owner_id', 'create_client_request_id', name='uq_agent_create_request'),)
    create_client_request_id: Mapped[UUID | None] = mapped_column(Uuid)
    create_fingerprint: Mapped[str | None] = mapped_column(String(64))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    config: Mapped[dict[str, object]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentRunAudit(Base):
    __tablename__ = 'agent_run_audits'
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), primary_key=True)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey('agents.id'))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    config: Mapped[dict[str, object]] = mapped_column(JSONB)
    skills: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    knowledge: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    execution: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    checkpoint_id: Mapped[str | None] = mapped_column(Text)


class AssistantConversation(Base):
    __tablename__ = 'assistant_conversations'
    __table_args__ = (
        UniqueConstraint('owner_id', 'create_request_id', name='uq_assistant_conversation_request'),
        Index(
            'ix_assistant_checkpoint_cleanup',
            'checkpoint_cleanup_after',
            'id',
            postgresql_where=text('deleted_at IS NOT NULL'),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey('agents.id'), index=True)
    create_request_id: Mapped[UUID] = mapped_column(Uuid)
    config: Mapped[dict[str, object]] = mapped_column(JSONB)
    skills: Mapped[dict[str, object]] = mapped_column(JSONB)
    runtime_version: Mapped[int | None] = mapped_column(Integer, default=1)
    checkpoint_id: Mapped[str | None] = mapped_column(Text)
    memory_run_id: Mapped[UUID | None] = mapped_column(Uuid)
    checkpoint_cleanup_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(100), default='新对话')
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssistantTurn(Base):
    __tablename__ = 'assistant_turns'
    __table_args__ = (
        UniqueConstraint('conversation_id', 'sequence', name='uq_assistant_turn_sequence'),
        UniqueConstraint('owner_id', 'request_id', name='uq_assistant_turn_request'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('assistant_conversations.id'), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    request_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), unique=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    attachments: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list, server_default='[]')
    sources: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssistantSummary(Base):
    """Explicit parent-attempt/child identity; native Runs own summary state and usage."""

    __tablename__ = 'assistant_summaries'
    parent_run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), primary_key=True)
    parent_attempt_id: Mapped[UUID] = mapped_column(Uuid)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), unique=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('assistant_conversations.id'), index=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
