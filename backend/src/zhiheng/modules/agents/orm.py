from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
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
    publication_count: Mapped[int] = mapped_column(Integer, default=0)
    published_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentVersion(Base):
    __tablename__ = 'agent_versions'
    __table_args__ = (UniqueConstraint('agent_id', 'version', name='uq_agent_publication'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey('agents.id'))
    version: Mapped[int] = mapped_column(Integer)
    draft_version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, object]] = mapped_column(JSONB)
    skills: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRunAudit(Base):
    __tablename__ = 'agent_run_audits'
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), primary_key=True)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey('agents.id'))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    config_version: Mapped[int] = mapped_column(Integer)
    published_version: Mapped[int | None] = mapped_column(Integer)
    config: Mapped[dict[str, object]] = mapped_column(JSONB)
    skills: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    knowledge: Mapped[list[dict[str, object]]] = mapped_column(JSONB)


class AssistantConversation(Base):
    __tablename__ = 'assistant_conversations'
    __table_args__ = (UniqueConstraint('owner_id', 'create_request_id', name='uq_assistant_conversation_request'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey('agents.id'), index=True)
    create_request_id: Mapped[UUID] = mapped_column(Uuid)
    published_version: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, object]] = mapped_column(JSONB)
    skills: Mapped[dict[str, object]] = mapped_column(JSONB)
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
    published_version: Mapped[int] = mapped_column(Integer)
    agent_name: Mapped[str] = mapped_column(String(100))
    sources: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
