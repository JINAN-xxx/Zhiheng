"""Identity-owned administration tables; schema changes are applied by explicit migration."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from zhiheng.infrastructure.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Versioned:
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IdentityDepartment(Versioned, Base):
    __tablename__ = 'identity_departments'
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey('identity_departments.id'), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    leader: Mapped[str] = mapped_column(String(100), default='')
    phone: Mapped[str] = mapped_column(String(40), default='')
    email: Mapped[str] = mapped_column(String(254), default='')
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class IdentityPost(Versioned, Base):
    __tablename__ = 'identity_posts'
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str] = mapped_column(String(2000), default='')


class IdentityRole(Versioned, Base):
    __tablename__ = 'identity_roles'
    code: Mapped[str] = mapped_column(String(80), unique=True)
    system_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    remark: Mapped[str] = mapped_column(String(2000), default='')


class IdentityMenu(Versioned, Base):
    __tablename__ = 'identity_menus'
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey('identity_menus.id'), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    path: Mapped[str] = mapped_column(String(200), unique=True)
    permission: Mapped[str] = mapped_column(String(100), default='')
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)


class IdentityUserRole(Base):
    __tablename__ = 'identity_user_roles'
    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(ForeignKey('identity_roles.id'), primary_key=True)


class IdentityUserPost(Base):
    __tablename__ = 'identity_user_posts'
    user_id: Mapped[UUID] = mapped_column(ForeignKey('identity_users.id'), primary_key=True)
    post_id: Mapped[UUID] = mapped_column(ForeignKey('identity_posts.id'), primary_key=True)
