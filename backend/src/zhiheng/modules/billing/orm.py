"""Billing-owned storage; cross-module references contain identifiers only."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class CreditAccount(Base):
    __tablename__ = 'billing_accounts'
    __table_args__ = (
        CheckConstraint(
            'allocated_points >= 0 AND reclaimed_points >= 0 AND used_points >= 0 AND reserved_points >= 0'
        ),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=0)
    allocated_points: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal(0))
    reclaimed_points: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal(0))
    used_points: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal(0))
    reserved_points: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal(0))


class CreditControl(Base):
    __tablename__ = 'billing_control'
    __table_args__ = (CheckConstraint('id = 1'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=0)


class CreditRate(Base):
    __tablename__ = 'billing_rates'
    __table_args__ = (
        UniqueConstraint('model_id', 'version'),
        CheckConstraint("billing_mode IN ('token','search_unit')"),
        CheckConstraint('input_points >= 0 AND output_points >= 0 AND cache_points >= 0 AND search_points >= 0'),
        CheckConstraint(
            '(is_free AND input_points = 0 AND output_points = 0 AND cache_points = 0 AND search_points = 0) OR (NOT is_free AND input_points + output_points + cache_points + search_points > 0)'
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    model_id: Mapped[UUID] = mapped_column(ForeignKey('models.id'), index=True)
    version: Mapped[int] = mapped_column(Integer)
    billing_mode: Mapped[str] = mapped_column(String(16))
    is_free: Mapped[bool] = mapped_column(Boolean)
    input_points: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    output_points: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    cache_points: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    search_points: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditCommand(Base):
    __tablename__ = 'billing_commands'
    __table_args__ = (UniqueConstraint('operator_id', 'client_request_id'),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    operator_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    client_request_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32))
    target: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    result: Mapped[dict[str, object]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditKeyMonth(Base):
    __tablename__ = 'billing_key_months'
    __table_args__ = (CheckConstraint('used_points >= 0 AND reserved_points >= 0'),)

    key_id: Mapped[UUID] = mapped_column(ForeignKey('developer_keys.id'), primary_key=True)
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    used_points: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal(0))
    reserved_points: Mapped[Decimal] = mapped_column(Numeric(38, 18), default=Decimal(0))


class CreditHold(Base):
    __tablename__ = 'billing_reservations'
    __table_args__ = (
        CheckConstraint("status IN ('reserved','unknown','settled')"),
        CheckConstraint('reserved_points >= 0 AND (charged_points IS NULL OR charged_points >= 0)'),
    )

    run_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    model_id: Mapped[UUID] = mapped_column(ForeignKey('models.id'))
    source: Mapped[str] = mapped_column(String(24))
    operation: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default='reserved')
    version: Mapped[int] = mapped_column(Integer, default=1)
    reserved_points: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    charged_points: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    rate_id: Mapped[UUID] = mapped_column(ForeignKey('billing_rates.id'))
    rate_version: Mapped[int] = mapped_column(Integer)
    rate_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB)
    request_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    usage_snapshot: Mapped[dict[str, int | None] | None] = mapped_column(JSONB)
    key_id: Mapped[UUID | None] = mapped_column(ForeignKey('developer_keys.id'), index=True)
    key_month: Mapped[str | None] = mapped_column(String(7))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_tokens: Mapped[int | None] = mapped_column(Integer)
    search_units: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CreditLedger(Base):
    __tablename__ = 'billing_ledger'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    operator_id: Mapped[UUID | None] = mapped_column(ForeignKey('identity_users.id'))
    kind: Mapped[str] = mapped_column(String(32))
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    available_delta: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    reserved_delta: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    available_after: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    reserved_after: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey('billing_reservations.run_id'), index=True)
    reason: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def schema_tables() -> tuple[Table, ...]:
    return tuple(
        Base.metadata.tables[model.__tablename__]
        for model in (
            CreditAccount,
            CreditControl,
            CreditRate,
            CreditCommand,
            CreditKeyMonth,
            CreditHold,
            CreditLedger,
        )
    )
