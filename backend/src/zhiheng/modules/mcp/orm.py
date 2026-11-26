"""MCP registrations and redacted invocation metadata."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base


class McpServer(Base):
    __tablename__ = 'mcp_servers'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(512), nullable=False)
    encrypted_token: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tools: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)


class McpCall(Base):
    __tablename__ = 'mcp_calls'
    __table_args__ = (
        Index('ix_mcp_calls_server_created', 'server_id', 'created_at'),
        CheckConstraint(
            '(run_id IS NOT NULL AND application_id IS NULL AND key_id IS NULL) OR '
            '(run_id IS NULL AND application_id IS NOT NULL AND key_id IS NOT NULL)',
            name='ck_mcp_call_origin',
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    server_id: Mapped[UUID] = mapped_column(ForeignKey('mcp_servers.id'), nullable=False)
    caller_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(Uuid)
    application_id: Mapped[UUID | None] = mapped_column(Uuid)
    key_id: Mapped[UUID | None] = mapped_column(Uuid)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class McpPublication(Base):
    __tablename__ = 'mcp_publications'
    __table_args__ = (
        Index(
            'uq_mcp_publication_active_name',
            'application_id',
            'name',
            unique=True,
            postgresql_where=text('withdrawn_at IS NULL'),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    application_id: Mapped[UUID] = mapped_column(ForeignKey('developer_applications.id'), index=True)
    server_id: Mapped[UUID] = mapped_column(ForeignKey('mcp_servers.id'))
    source_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    name: Mapped[str] = mapped_column(String(120))
    definition: Mapped[dict[str, object]] = mapped_column(JSONB)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
