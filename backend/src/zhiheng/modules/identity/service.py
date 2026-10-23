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

from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024


class IdentityService:
    async def is_active(self, session: AsyncSession, user_id: UUID) -> bool:
        user = await session.get(IdentityUser, user_id)
        return user is not None and user.is_active

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
        password_hash = await asyncio.to_thread(self._hasher.hash, password)
        user = IdentityUser(username=username, password_hash=password_hash, is_admin=is_admin)
        session.add(user)
        await session.flush()
        return Principal(user.id, user.username, user.is_admin)

    async def login(self, session: AsyncSession, *, username: str, password: str) -> tuple[Principal, str]:
        user = await session.scalar(select(IdentityUser).where(IdentityUser.username == username))
        encoded = user.password_hash if user is not None else self._dummy_hash
        try:
            await asyncio.to_thread(self._hasher.verify, encoded, password)
        except (VerificationError, InvalidHashError) as exc:
            raise AppError('invalid_credentials', '用户名或密码错误', 401) from exc
        if user is None or not user.is_active:
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
        return Principal(user.id, user.username, user.is_admin), token

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
            )
        )
        if lock:
            query = query.with_for_update(read=True)
        user = await session.scalar(query)
        if user is None:
            raise AppError('unauthenticated', '登录状态已失效，请重新登录', 401)
        return Principal(user.id, user.username, user.is_admin)

    async def logout(self, session: AsyncSession, token: str | None) -> None:
        if token:
            await session.execute(delete(IdentitySession).where(IdentitySession.token_hash == self._hash_token(token)))

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()
