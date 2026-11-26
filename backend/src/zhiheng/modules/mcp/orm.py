"""MCP registrations and redacted invocation metadata."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
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
    __table_args__ = (Index('ix_mcp_calls_server_created', 'server_id', 'created_at'),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    server_id: Mapped[UUID] = mapped_column(ForeignKey('mcp_servers.id'), nullable=False)
    caller_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
