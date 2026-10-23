from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class UsageAccount(Base):
    __tablename__ = 'developer_accounts'
    __table_args__ = (CheckConstraint('used_tokens >= 0 AND reserved_tokens >= 0 AND limit_tokens > 0'),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), primary_key=True)
    limit_tokens: Mapped[int] = mapped_column(Integer)
    used_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reserved_tokens: Mapped[int] = mapped_column(Integer, default=0)


class Invocation(Base):
    __tablename__ = 'developer_invocations'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    model_id: Mapped[UUID] = mapped_column(ForeignKey('models.id'))
    source: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default='pending')
    reserved_tokens: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    charged_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
