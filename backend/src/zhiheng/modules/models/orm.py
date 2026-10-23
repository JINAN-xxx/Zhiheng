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
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class ModelConnection(Base):
    __tablename__ = 'model_connections'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80))
    base_url: Mapped[str] = mapped_column(String(2048))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelDefinition(Base):
    __tablename__ = 'models'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey('model_connections.id'))
    name: Mapped[str] = mapped_column(String(80))
    upstream_model: Mapped[str] = mapped_column(String(200))
    max_output_tokens: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    model_type: Mapped[str] = mapped_column(String(16), default='chat')
    vision_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConnectionRevision(Base):
    __tablename__ = 'model_connection_revisions'
    __table_args__ = (UniqueConstraint('connection_id', 'version'),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    connection_id: Mapped[UUID] = mapped_column(ForeignKey('model_connections.id'))
    version: Mapped[int] = mapped_column(Integer)
    base_url: Mapped[str] = mapped_column(String(2048))
    encrypted_api_key: Mapped[str] = mapped_column(Text)


class RateCard(Base):
    __tablename__ = 'model_rate_cards'
    __table_args__ = (
        UniqueConstraint('model_id', 'version'),
        CheckConstraint("billing_mode IN ('token','search_unit')"),
        CheckConstraint('input_price >= 0 AND output_price >= 0 AND cache_price >= 0 AND search_price >= 0'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    model_id: Mapped[UUID] = mapped_column(ForeignKey('models.id'))
    version: Mapped[int] = mapped_column(Integer)
    billing_mode: Mapped[str] = mapped_column(String(16))
    input_price: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    output_price: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    cache_price: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    search_price: Mapped[Decimal] = mapped_column(Numeric(30, 12))
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
