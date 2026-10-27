from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class KnowledgeBase(Base):
    __tablename__ = 'knowledge_bases'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default='')
    access: Mapped[str] = mapped_column(String(16), default='private')
    version: Mapped[int] = mapped_column(Integer, default=1)
    acl_version: Mapped[int] = mapped_column(Integer, default=1)
    embed_model_id: Mapped[UUID] = mapped_column(ForeignKey('models.id'))
    chunk_size: Mapped[int] = mapped_column(Integer, default=1000)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=200)
    active_revision: Mapped[UUID | None] = mapped_column(Uuid)
    active_job_id: Mapped[UUID | None] = mapped_column(Uuid)
    last_error: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeGrant(Base):
    __tablename__ = 'knowledge_grants'
    base_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_bases.id'), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), primary_key=True)
    effect: Mapped[str] = mapped_column(String(8))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeFolder(Base):
    __tablename__ = 'knowledge_folders'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    base_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey('knowledge_folders.id'))
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeUpload(Base):
    __tablename__ = 'knowledge_uploads'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    base_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'), unique=True)
    folder_id: Mapped[UUID | None] = mapped_column(ForeignKey('knowledge_folders.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeDocument(Base):
    __tablename__ = 'knowledge_documents'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    base_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'))
    folder_id: Mapped[UUID | None] = mapped_column(ForeignKey('knowledge_folders.id'))
    file_name: Mapped[str] = mapped_column(String(255))
    file_size: Mapped[int] = mapped_column(Integer)
    mime: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    active_revision: Mapped[UUID | None] = mapped_column(Uuid)
    job_id: Mapped[UUID | None] = mapped_column(Uuid)
    artifact_file_id: Mapped[UUID | None] = mapped_column(ForeignKey('files.id'))
    source_sha256: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int | None] = mapped_column(Integer)
    chunk_size: Mapped[int] = mapped_column(Integer)
    chunk_overlap: Mapped[int] = mapped_column(Integer)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IndexGeneration(Base):
    __tablename__ = 'knowledge_generations'
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    base_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    attempt_id: Mapped[UUID] = mapped_column(Uuid)
    collection_name: Mapped[str] = mapped_column(String(160), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='candidate')
    binding: Mapped[dict[str, object]] = mapped_column(JSONB)
    fingerprint: Mapped[str] = mapped_column(String(64))
    dimension: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(Integer)
    document_versions: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    gc_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = 'knowledge_chunks'
    generation_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_generations.id'), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_documents.id'), index=True)
    document_revision: Mapped[UUID] = mapped_column(Uuid)
    file_id: Mapped[UUID] = mapped_column(ForeignKey('files.id'))
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    model_text: Mapped[str] = mapped_column(Text)
    vector: Mapped[list[float] | None] = mapped_column(JSONB(none_as_null=True))  # type: ignore[no-untyped-call]


class KnowledgeJob(Base):
    __tablename__ = 'knowledge_jobs'
    __table_args__ = (UniqueConstraint('actor_id', 'client_request_id'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    base_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_bases.id'), index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'))
    client_request_id: Mapped[UUID] = mapped_column(Uuid)
    fingerprint: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default='pending')
    phase: Mapped[str] = mapped_column(String(24), default='parse')
    version: Mapped[int] = mapped_column(Integer, default=1)
    attempt_id: Mapped[UUID] = mapped_column(Uuid, default=uuid4)
    index_revision: Mapped[UUID] = mapped_column(Uuid, default=uuid4)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tick_token: Mapped[UUID | None] = mapped_column(Uuid)
    documents: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetrievalIntent(Base):
    __tablename__ = 'knowledge_retrieval_intents'
    __table_args__ = (UniqueConstraint('namespace_id', 'client_request_id'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    namespace_id: Mapped[UUID] = mapped_column(Uuid)
    client_request_id: Mapped[UUID] = mapped_column(Uuid)
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    owner_id: Mapped[UUID] = mapped_column(Uuid)
    key_id: Mapped[UUID | None] = mapped_column(Uuid)
    application_id: Mapped[UUID | None] = mapped_column(Uuid)
    resource_type: Mapped[str] = mapped_column(String(24))
    resource_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    request: Mapped[dict[str, object]] = mapped_column(JSONB)
    dependencies: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    phase: Mapped[str] = mapped_column(String(24), default='embedding')
    hits: Mapped[list[dict[str, object]]] = mapped_column(JSONB, default=list)
    main_run_id: Mapped[UUID | None] = mapped_column(ForeignKey('runtime_runs.id'))
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tick_token: Mapped[UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeStep(Base):
    __tablename__ = 'knowledge_steps'
    __table_args__ = (UniqueConstraint('parent_id', 'step_key'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    parent_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    step_key: Mapped[str] = mapped_column(String(200))
    run_id: Mapped[UUID] = mapped_column(ForeignKey('runtime_runs.id'), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))


class GenerationPin(Base):
    __tablename__ = 'knowledge_generation_pins'
    intent_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_retrieval_intents.id'), primary_key=True)
    generation_id: Mapped[UUID] = mapped_column(ForeignKey('knowledge_generations.id'), primary_key=True)
