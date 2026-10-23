from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class Run(Base):
    __tablename__ = 'runtime_runs'
    __table_args__ = (
        UniqueConstraint('owner_id', 'client_request_id', name='uq_run_client_request'),
        CheckConstraint(
            "status IN ('pending','running','cancel_requested','completed','failed','cancelled','interrupted')"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    billing_owner_id: Mapped[UUID] = mapped_column(Uuid)
    resource_type: Mapped[str] = mapped_column(String(24))
    resource_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    client_request_id: Mapped[UUID] = mapped_column(Uuid)
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    attempt_id: Mapped[UUID | None] = mapped_column(Uuid)
    attempt_no: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    projection_seq: Mapped[int] = mapped_column(Integer, default=0)
    provider_started: Mapped[bool] = mapped_column(Boolean, default=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text, default='')
    reasoning: Mapped[str] = mapped_column(Text, default='', server_default='')
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    finish_reason: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(300))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    usage_status: Mapped[str] = mapped_column(String(16), default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Outbox(Base):
    __tablename__ = 'runtime_outbox'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), unique=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
