from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base
from zhiheng.modules.identity.admin_orm import utcnow


class IdentityUser(Base):
    __tablename__ = 'identity_users'

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    display_name: Mapped[str] = mapped_column(String(100), default='')
    email: Mapped[str] = mapped_column(String(254), default='')
    phone: Mapped[str] = mapped_column(String(40), default='')
    department_id: Mapped[UUID | None] = mapped_column(ForeignKey('identity_departments.id'), nullable=True, index=True)
    remark: Mapped[str] = mapped_column(String(2000), default='')
    version: Mapped[int] = mapped_column(Integer, default=1)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Historical rows did not store these timestamps; migration leaves them unknown.
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=True)


class IdentitySession(Base):
    __tablename__ = 'identity_sessions'

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id', ondelete='CASCADE'), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    id: Mapped[UUID] = mapped_column(Uuid, unique=True, default=uuid4)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=True)
