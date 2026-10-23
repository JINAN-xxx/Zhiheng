from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
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
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey('developer_applications.id'), index=True)
    key_id: Mapped[UUID | None] = mapped_column(ForeignKey('developer_keys.id'), index=True)
    operation: Mapped[str] = mapped_column(String(16), default='chat')
    rate_card_id: Mapped[UUID | None] = mapped_column(ForeignKey('model_rate_cards.id'))
    rate_version: Mapped[int | None] = mapped_column(Integer)
    rate_snapshot: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    currency: Mapped[str | None] = mapped_column(String(3))
    reserved_cost: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    billable_cost: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    search_units: Mapped[int | None] = mapped_column(Integer)
    cache_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    quota_minute: Mapped[int | None] = mapped_column(Integer)
    quota_month: Mapped[str | None] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DeveloperApplication(Base):
    __tablename__ = 'developer_applications'
    __table_args__ = (CheckConstraint("status IN ('active','disabled')"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    status: Mapped[str] = mapped_column(String(16), default='active')
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeveloperKey(Base):
    __tablename__ = 'developer_keys'
    __table_args__ = (
        CheckConstraint("status IN ('active','retiring','revoked')"),
        CheckConstraint(
            '(rpm_limit IS NULL OR rpm_limit > 0) AND (tpm_limit IS NULL OR tpm_limit > 0) AND (concurrency_limit IS NULL OR concurrency_limit > 0) AND (monthly_budget IS NULL OR monthly_budget >= 0)'
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey('developer_applications.id'), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(32))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default='active')
    model_ids: Mapped[list[str]] = mapped_column(JSONB)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    tpm_limit: Mapped[int | None] = mapped_column(Integer)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    currency: Mapped[str] = mapped_column(String(3), default='USD')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KeyMinuteBucket(Base):
    __tablename__ = 'developer_minute_buckets'
    __table_args__ = (UniqueConstraint('key_id', 'minute'), CheckConstraint('requests >= 0 AND tokens >= 0'))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    key_id: Mapped[UUID] = mapped_column(ForeignKey('developer_keys.id'))
    minute: Mapped[int] = mapped_column(Integer)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
