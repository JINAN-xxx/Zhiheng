"""Graph configuration, durable attempts, immutable generations and reader pins."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class GraphConfiguration(Base):
    __tablename__ = 'knowledge_graph_configs'
    __table_args__ = (
        CheckConstraint(
            'version>=1 AND concurrency BETWEEN 1 AND 4 AND timeout_seconds BETWEEN 30 AND 600 AND max_output_tokens BETWEEN 1024 AND 32768'
        ),
        CheckConstraint("reasoning_strategy IN ('auto','default','low','disabled')"),
    )
    base_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_bases.id'), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    llm_model_id: Mapped[UUID] = mapped_column(ForeignKey('models.id'))
    reasoning_strategy: Mapped[str] = mapped_column(String(16), default='default')
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=240)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=16384)
    concurrency: Mapped[int] = mapped_column(Integer, default=2)
    active_revision: Mapped[UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GraphJob(Base):
    __tablename__ = 'knowledge_graph_jobs'
    __table_args__ = (
        UniqueConstraint('actor_id', 'client_request_id'),
        CheckConstraint("operation IN ('build','preview','query','gc')"),
        CheckConstraint(
            "status IN ('pending','running','waiting_model','draining','completed','failed','cancelled','interrupted')"
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    base_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    client_request_id: Mapped[UUID] = mapped_column(Uuid)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(24), default='pending', index=True)
    phase: Mapped[str] = mapped_column(String(32), default='queued')
    version: Mapped[int] = mapped_column(Integer, default=1)
    attempt_id: Mapped[UUID] = mapped_column(Uuid)
    graph_revision: Mapped[UUID | None] = mapped_column(Uuid)
    frozen: Mapped[dict[str, object]] = mapped_column(JSONB)
    request: Mapped[dict[str, object]] = mapped_column(JSONB)
    processed_documents: Mapped[int | None] = mapped_column(Integer)
    total_documents: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GraphAttempt(Base):
    __tablename__ = 'knowledge_graph_attempts'
    __table_args__ = (
        CheckConstraint('epoch>=1 AND next_ordinal>=0'),
        CheckConstraint('NOT writer_stopped OR stopped_at IS NOT NULL'),
        CheckConstraint("status IN ('pending','running','draining','completed','failed','cancelled','interrupted')"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_graph_jobs.id'), unique=True)
    epoch: Mapped[int] = mapped_column(Integer, default=1)
    owner_token: Mapped[UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(24), default='pending')
    workspace: Mapped[str] = mapped_column(String(96), unique=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_ordinal: Mapped[int] = mapped_column(Integer, default=0)
    writer_stopped: Mapped[bool] = mapped_column(Boolean, default=False)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GraphGeneration(Base):
    __tablename__ = 'knowledge_graph_generations'
    __table_args__ = (CheckConstraint("status IN ('candidate','active','retired','collecting','collected')"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    base_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_graph_attempts.id'), unique=True)
    status: Mapped[str] = mapped_column(String(24), default='candidate')
    fingerprint: Mapped[str] = mapped_column(String(64))
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB)
    frozen: Mapped[dict[str, object]] = mapped_column(JSONB)
    gc_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GraphStep(Base):
    __tablename__ = 'knowledge_graph_steps'
    __table_args__ = (
        UniqueConstraint('attempt_id', 'role', 'operation', 'ordinal'),
        CheckConstraint('epoch>=1 AND ordinal>=1'),
        CheckConstraint("role IN ('extract','keyword','query','vlm','embedding','rerank')"),
        CheckConstraint("operation IN ('generation','keyword','embedding','rerank')"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    attempt_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_graph_attempts.id'), index=True)
    epoch: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(24))
    operation: Mapped[str] = mapped_column(String(24))
    ordinal: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))


class GraphPin(Base):
    __tablename__ = 'knowledge_graph_pins'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    generation_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_graph_generations.id'), index=True)
    job_id: Mapped[UUID | None] = mapped_column(ForeignKey('knowledge_graph_jobs.id'))
    owner_token: Mapped[UUID] = mapped_column(Uuid)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
