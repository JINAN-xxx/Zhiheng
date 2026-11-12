from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.modules.chat.actions.contracts import FeedbackValue
from zhiheng.shared.time import utcnow


class MessageFeedback(Base):
    __tablename__ = 'chat_message_feedback'
    __table_args__ = (CheckConstraint("value IN ('up', 'down')", name='ck_chat_message_feedback_value'),)

    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), primary_key=True)
    message_id: Mapped[UUID] = mapped_column(ForeignKey('chat_messages.id'), primary_key=True, index=True)
    value: Mapped[FeedbackValue] = mapped_column(String(4))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageShare(Base):
    __tablename__ = 'chat_message_shares'
    __table_args__ = (
        Index('ix_chat_message_shares_owner_message', 'owner_id', 'message_id', 'created_at'),
        CheckConstraint('expires_at > created_at', name='ck_chat_message_shares_expiry'),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    message_id: Mapped[UUID] = mapped_column(ForeignKey('chat_messages.id'), index=True)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey('chat_conversations.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    question: Mapped[str | None] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    include_question: Mapped[bool] = mapped_column(Boolean)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
