from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.shared.time import utcnow


class Versioned:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Dictionary(Versioned, Base):
    __tablename__ = 'operations_dictionaries'
    __table_args__ = (CheckConstraint('version >= 1', name='ck_operations_dictionary_version'),)
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str] = mapped_column(String(500), default='')


class DictionaryItem(Versioned, Base):
    __tablename__ = 'operations_dictionary_items'
    __table_args__ = (
        UniqueConstraint('dictionary_id', 'value', name='uq_operations_dictionary_item_value'),
        CheckConstraint('version >= 1', name='ck_operations_item_version'),
    )
    dictionary_id: Mapped[UUID] = mapped_column(
        ForeignKey('operations_dictionaries.id', ondelete='RESTRICT'), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str] = mapped_column(String(500), default='')


class Config(Versioned, Base):
    __tablename__ = 'operations_configs'
    __table_args__ = (CheckConstraint('version >= 1', name='ck_operations_config_version'),)
    key: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    value: Mapped[str] = mapped_column(String(1000))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str] = mapped_column(String(500), default='')


class Notice(Versioned, Base):
    __tablename__ = 'operations_notices'
    __table_args__ = (
        CheckConstraint("kind IN ('notice', 'announcement')", name='ck_operations_notice_kind'),
        CheckConstraint("status IN ('draft', 'published')", name='ck_operations_notice_status'),
        CheckConstraint('version >= 1', name='ck_operations_notice_version'),
    )
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default='notice')
    status: Mapped[str] = mapped_column(String(16), default='draft', index=True)
    remark: Mapped[str] = mapped_column(String(500), default='')


class OperationLog(Base):
    __tablename__ = 'operations_logs'
    __table_args__ = (
        CheckConstraint("kind IN ('login', 'operation')", name='ck_operations_log_kind'),
        CheckConstraint("outcome IN ('success', 'failed')", name='ck_operations_log_outcome'),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str] = mapped_column(String(80))
    username: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(80), default='')
    resource_id: Mapped[str | None] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(16), index=True)
    message: Mapped[str] = mapped_column(String(500), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
