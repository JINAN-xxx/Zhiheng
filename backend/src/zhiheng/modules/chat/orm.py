from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class Conversation(Base):
    __tablename__ = 'chat_conversations'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    title: Mapped[str] = mapped_column(String(100), default='新对话')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    __table_args__ = (UniqueConstraint('run_id', 'role', name='uq_chat_run_role'),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('chat_conversations.id'), index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text, default='', server_default='')
    skill_snapshots: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list, server_default='[]')
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
