from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class WorkTask(Base):
    __tablename__ = 'work_tasks'
    __table_args__ = (
        UniqueConstraint('owner_id', 'create_client_request_id', name='uq_work_create'),
        Index(
            'ix_work_checkpoint_cleanup',
            'checkpoint_cleanup_after',
            'id',
            postgresql_where=text('deleted_at IS NOT NULL'),
        ),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    title: Mapped[str] = mapped_column(String(100))
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default='active')
    mode: Mapped[str] = mapped_column(String(16), default='legacy', server_default='legacy')
    configuration: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict, server_default='{}')
    execution_state: Mapped[str] = mapped_column(String(24), default='idle', server_default='idle')
    plan: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list, server_default='[]')
    agent_checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    workspace_files: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list, server_default='[]')
    checkpoint_cleanup_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    current_run_id: Mapped[UUID | None] = mapped_column(ForeignKey('runtime_runs.id'))
    active_agent_id: Mapped[UUID | None] = mapped_column(Uuid)
    create_active_agent_id: Mapped[UUID | None] = mapped_column(Uuid)
    create_client_request_id: Mapped[UUID] = mapped_column(Uuid)
    create_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkMember(Base):
    __tablename__ = 'work_members'
    __table_args__ = (
        Index('uq_work_active_member', 'task_id', 'agent_id', unique=True, postgresql_where=text('removed_at IS NULL')),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey('work_tasks.id'), index=True)
    agent_id: Mapped[UUID] = mapped_column(ForeignKey('agents.id'))
    agent_owner_id: Mapped[UUID] = mapped_column(Uuid)
    agent_name_snapshot: Mapped[str] = mapped_column(String(100))
    agent_revision: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict[str, object]] = mapped_column(JSONB)
    skills: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkMessage(Base):
    __tablename__ = 'work_messages'
    __table_args__ = (
        UniqueConstraint('task_id', 'sequence', name='uq_work_message_sequence'),
        UniqueConstraint('task_id', 'client_request_id', 'role', name='uq_work_request_role'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey('work_tasks.id'), index=True)
    client_request_id: Mapped[UUID] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default='')
    knowledge_sources: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list, server_default='[]')
    reasoning: Mapped[str] = mapped_column(Text, default='')
    status: Mapped[str] = mapped_column(String(20), default='completed')
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey('runtime_runs.id'), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    audit: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    usage: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    agent_id: Mapped[UUID | None] = mapped_column(Uuid)
    agent_name_snapshot: Mapped[str | None] = mapped_column(String(100))
    agent_revision: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkInput(Base):
    __tablename__ = 'work_inputs'
    __table_args__ = (UniqueConstraint('task_id', 'name', 'version', name='uq_work_input_version'),)
    task_id: Mapped[UUID] = mapped_column(ForeignKey('work_tasks.id'), primary_key=True)
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    file_version: Mapped[int] = mapped_column(Integer)
    acl_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkStep(Base):
    __tablename__ = 'work_steps'
    __table_args__ = (UniqueConstraint('task_id', 'action_id', name='uq_work_step_action'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey('work_tasks.id'), index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), index=True)
    attempt_id: Mapped[UUID] = mapped_column(Uuid)
    action_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default='pending')
    summary: Mapped[str] = mapped_column(Text, default='')
    details: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkArtifact(Base):
    __tablename__ = 'work_artifacts'
    __table_args__ = (UniqueConstraint('task_id', 'logical_path', 'version', name='uq_work_artifact_version'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey('work_tasks.id'), index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), index=True)
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'))
    preview_file_id: Mapped[UUID | None] = mapped_column(ForeignKey('files.id'))
    logical_path: Mapped[str] = mapped_column(String(500))
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64))
    validation: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkSandboxLease(Base):
    __tablename__ = 'work_sandbox_leases'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey('work_tasks.id'), index=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), index=True)
    attempt_id: Mapped[UUID] = mapped_column(Uuid)
    sandbox_id: Mapped[str | None] = mapped_column(String(200))
    image: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(24), default='creating')
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[str | None] = mapped_column(String(500))
