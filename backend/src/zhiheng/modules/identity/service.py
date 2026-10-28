import asyncio
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.identity.admin_orm import IdentityRole, IdentityUserRole
from zhiheng.modules.identity.admin_permissions import PERMISSION_CODES
from zhiheng.modules.identity.contracts import IdentityInfo
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024


class IdentityService:
    async def directory(self, session: AsyncSession, *, search: str = '') -> list[IdentityInfo]:
        query = select(IdentityUser).where(IdentityUser.is_active.is_(True), IdentityUser.deleted_at.is_(None))
        if search:
            escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            query = query.where(IdentityUser.username.ilike(f'%{escaped}%', escape='\\'))
        return [
            IdentityInfo(id=row.id, username=row.username)
            for row in await session.scalars(query.order_by(IdentityUser.username).limit(100))
        ]

    async def matching_user_ids(self, session: AsyncSession, *, search: str) -> tuple[UUID, ...]:
        """Complete identity name matches for business filtering, including inactive historical owners."""
        escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        return tuple(
            await session.scalars(
                select(IdentityUser.id)
                .where(IdentityUser.username.ilike(f'%{escaped}%', escape='\\'))
                .order_by(IdentityUser.id)
            )
        )

    async def principal_for_user(self, session: AsyncSession, user_id: UUID) -> Principal:
        user = await session.scalar(
            select(IdentityUser).where(IdentityUser.id == user_id).execution_options(populate_existing=True)
        )
        if user is None or not user.is_active or user.deleted_at is not None:
            raise AppError('work_owner_unavailable', '任务所有者不可用', 404)
        return await self._principal(session, user)

    async def is_active(self, session: AsyncSession, user_id: UUID) -> bool:
        user = await session.scalar(
            select(IdentityUser).where(IdentityUser.id == user_id).execution_options(populate_existing=True)
        )
        return user is not None and user.is_active and user.deleted_at is None

    async def _principal(self, session: AsyncSession, user: IdentityUser) -> Principal:
        permissions = await session.scalars(
            select(IdentityRole.permissions)
            .join(IdentityUserRole)
            .where(
                IdentityUserRole.user_id == user.id,
                IdentityRole.is_active.is_(True),
            )
        )
        granted = frozenset(code for group in permissions for code in group if code in PERMISSION_CODES)
        return Principal(user.id, user.username, user.is_admin, granted)

    async def hash_password(self, password: str) -> str:
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            raise AppError('invalid_identity', '密码长度须为 12 到 1024 个字符')
        return await asyncio.to_thread(self._hasher.hash, password)

    def __init__(self, session_ttl_seconds: int = 86400) -> None:
        self._hasher = PasswordHasher()
        self._session_ttl = session_ttl_seconds
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))

    async def create_user(
        self, session: AsyncSession, *, username: str, password: str, is_admin: bool = False
    ) -> Principal:
        if (
            not re.fullmatch(r'[a-zA-Z0-9_.@-]{3,80}', username)
            or not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH
        ):
            raise AppError('invalid_identity', '用户名无效或密码不足 12 个字符')
        existing = await session.scalar(select(IdentityUser.id).where(IdentityUser.username == username))
        if existing is not None:
            raise AppError('username_exists', '用户名已存在', 409)
        password_hash = await self.hash_password(password)
        user = IdentityUser(username=username, password_hash=password_hash, is_admin=is_admin)
        session.add(user)
        await session.flush()
        return Principal(user.id, user.username, user.is_admin)

    async def login(self, session: AsyncSession, *, username: str, password: str) -> tuple[Principal, str]:
        user = await session.scalar(
            select(IdentityUser).where(IdentityUser.username == username).execution_options(populate_existing=True)
        )
        encoded = user.password_hash if user is not None else self._dummy_hash
        try:
            await asyncio.to_thread(self._hasher.verify, encoded, password)
        except (VerificationError, InvalidHashError) as exc:
            raise AppError('invalid_credentials', '用户名或密码错误', 401) from exc
        if user is None:
            raise AppError('invalid_credentials', '用户名或密码错误', 401)
        # Argon2 runs without a database lock. Recheck under the same user lock used
        # by password resets and deactivation, retaining it until the caller commits.
        user = await session.scalar(
            select(IdentityUser)
            .where(IdentityUser.id == user.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if user is None or not user.is_active or user.deleted_at is not None or user.password_hash != encoded:
            raise AppError('invalid_credentials', '用户名或密码错误', 401)
        token = secrets.token_urlsafe(32)
        session.add(
            IdentitySession(
                token_hash=self._hash_token(token),
                user_id=user.id,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._session_ttl),
            )
        )
        await session.flush()
        return await self._principal(session, user), token

    async def authenticate(self, session: AsyncSession, token: str | None, *, lock: bool = False) -> Principal:
        if not token:
            raise AppError('unauthenticated', '请先登录', 401)
        query = (
            select(IdentityUser)
            .join(IdentitySession, IdentitySession.user_id == IdentityUser.id)
            .where(
                IdentitySession.token_hash == self._hash_token(token),
                IdentitySession.expires_at > datetime.now(timezone.utc),
                IdentityUser.is_active.is_(True),
                IdentityUser.deleted_at.is_(None),
            )
        )
        if lock:
            query = query.with_for_update(read=True)
        user = await session.scalar(query.execution_options(populate_existing=True))
        if user is None:
            raise AppError('unauthenticated', '登录状态已失效，请重新登录', 401)
        return await self._principal(session, user)

    async def logout(self, session: AsyncSession, token: str | None) -> None:
        if token:
            await session.execute(delete(IdentitySession).where(IdentitySession.token_hash == self._hash_token(token)))

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
