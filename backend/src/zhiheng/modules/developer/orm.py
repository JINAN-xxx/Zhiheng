from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class UsageAccount(Base):
    __tablename__ = 'developer_accounts'
    __table_args__ = (CheckConstraint('used_tokens >= 0 AND reserved_tokens >= 0 AND limit_tokens > 0'),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), primary_key=True)
    limit_tokens: Mapped[int] = mapped_column(BigInteger)
    used_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, default=0)


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
    points_enforced: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
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
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')"),
        CheckConstraint('source_file_limit > 0 AND source_byte_limit > 0', name='ck_application_source_limits'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    status: Mapped[str] = mapped_column(String(16), default='active')
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_file_limit: Mapped[int] = mapped_column(Integer, default=1000, server_default='1000')
    source_byte_limit: Mapped[int] = mapped_column(BigInteger, default=1073741824, server_default='1073741824')


class DeveloperKey(Base):
    __tablename__ = 'developer_keys'
    __table_args__ = (
        CheckConstraint("status IN ('active','retiring','revoked')"),
        CheckConstraint('resource_rpm_limit > 0 AND resource_rpm_limit <= 120', name='ck_key_resource_rpm'),
        CheckConstraint(
            '(rpm_limit IS NULL OR rpm_limit > 0) AND (tpm_limit IS NULL OR tpm_limit > 0) AND (concurrency_limit IS NULL OR concurrency_limit > 0) AND (monthly_budget IS NULL OR monthly_budget >= 0)'
        ),
        CheckConstraint(
            'monthly_points_limit IS NULL OR monthly_points_limit >= 0',
            name='ck_developer_keys_monthly_points_limit',
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey('developer_applications.id'), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(32))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default='active')
    model_ids: Mapped[list[str]] = mapped_column(JSONB)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, default=lambda: ['models:invoke', 'agents:invoke'], server_default='["models:invoke","agents:invoke"]'
    )
    resource_rpm_limit: Mapped[int] = mapped_column(Integer, default=120, server_default='120')
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    tpm_limit: Mapped[int | None] = mapped_column(Integer)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer)
    monthly_budget: Mapped[Decimal | None] = mapped_column(Numeric(30, 12))
    monthly_points_limit: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
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
    tokens: Mapped[int] = mapped_column(BigInteger, default=0)


class ResourceGrant(Base):
    __tablename__ = 'developer_resource_grants'
    __table_args__ = (CheckConstraint("kind IN ('file','knowledge_base','skill')"),)
    application_id: Mapped[UUID] = mapped_column(ForeignKey('developer_applications.id'), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    resource_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    actions: Mapped[list[str]] = mapped_column(JSONB)
    resource_owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    ownership_stamp: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceEnrollment(Base):
    """Permanent original-file accounting fact, independent of mutable access grants."""

    __tablename__ = 'developer_source_enrollments'
    __table_args__ = (CheckConstraint('size >= 0'),)
    application_id: Mapped[UUID] = mapped_column(ForeignKey('developer_applications.id'), primary_key=True)
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'), primary_key=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResourceMinuteBucket(Base):
    __tablename__ = 'developer_resource_minute_buckets'
    __table_args__ = (CheckConstraint('requests >= 0'),)
    key_id: Mapped[UUID] = mapped_column(ForeignKey('developer_keys.id'), primary_key=True)
    minute: Mapped[int] = mapped_column(Integer, primary_key=True)
    requests: Mapped[int] = mapped_column(Integer, default=0)


class APIRequest(Base):
    """Permanent deduplication and origin, never a second native task state machine."""

    __tablename__ = 'developer_api_requests'
    __table_args__ = (UniqueConstraint('application_id', 'operation', 'idempotency_key'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey('developer_applications.id'), index=True)
    key_id: Mapped[UUID] = mapped_column(ForeignKey('developer_keys.id'))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    operation: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[UUID] = mapped_column(Uuid)
    semantic_hash: Mapped[str] = mapped_column(String(64))
    required_scope: Mapped[str] = mapped_column(String(32))
    resources: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    result_kind: Mapped[str | None] = mapped_column(String(32))
    result_id: Mapped[UUID | None] = mapped_column(Uuid)
    snapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    snapshot_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
