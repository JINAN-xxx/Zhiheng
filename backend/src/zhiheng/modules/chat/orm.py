from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class Conversation(Base):
    __tablename__ = 'chat_conversations'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(String(100), default='新对话')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    __table_args__ = (
        UniqueConstraint('run_id', 'role', name='uq_chat_run_role'),
        UniqueConstraint('reply_to_message_id', 'generation_index', name='uq_chat_reply_generation'),
        CheckConstraint('generation_index >= 1', name='ck_chat_generation_index'),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('chat_conversations.id'), index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'))
    role: Mapped[str] = mapped_column(String(16))
    reply_to_message_id: Mapped[UUID | None] = mapped_column(ForeignKey('chat_messages.id'), nullable=True, index=True)
    generation_index: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    request_context: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, server_default='{}')
    content: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text, default='', server_default='')
    skill_snapshots: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list, server_default='[]')
    attachments: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list, server_default='[]')
    knowledge_sources: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list, server_default='[]')
    attachment_inputs: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list, server_default='[]')
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
