import asyncio
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.identity.admin_orm import (
    IdentityDepartment,
    IdentityRole,
    IdentityUserRole,
)
from zhiheng.modules.identity.admin_permissions import PERMISSION_CODES
from zhiheng.modules.identity.builtin_roles import sync_builtin_role_membership
from zhiheng.modules.identity.contracts import (
    IdentityDirectoryEntry,
    IdentityDirectoryPage,
    IdentityInfo,
    ResourceOwnerInfo,
)
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.modules.identity.profile_contracts import PasswordChange, ProfileInfo, ProfilePatch
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
MAX_DIRECTORY_PAGE_SIZE = 200
MAX_DIRECTORY_SEARCH_LENGTH = 200
MAX_RELATED_USERS = 101


class IdentityService:
    async def _profile_user(self, session: AsyncSession, user_id: UUID, *, lock: bool = False) -> IdentityUser:
        query = select(IdentityUser).where(IdentityUser.id == user_id)
        if lock:
            query = query.with_for_update()
        user = await session.scalar(query.execution_options(populate_existing=True))
        if user is None or not user.is_active or user.deleted_at is not None:
            raise AppError('unauthenticated', '登录状态已失效，请重新登录', 401)
        return user

    async def profile(self, session: AsyncSession, *, user_id: UUID) -> ProfileInfo:
        user = await self._profile_user(session, user_id)
        department_name = (
            await session.scalar(select(IdentityDepartment.name).where(IdentityDepartment.id == user.department_id))
            if user.department_id is not None
            else None
        )
        roles = await session.scalars(
            select(IdentityRole.name)
            .join(IdentityUserRole)
            .where(IdentityUserRole.user_id == user.id)
            .order_by(IdentityRole.name, IdentityRole.id)
        )
        return ProfileInfo(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            phone=user.phone,
            is_admin=user.is_admin,
            department_name=department_name,
            roles=list(roles),
            created_at=user.created_at,
            version=user.version,
        )

    async def _profile_cas(
        self,
        session: AsyncSession,
        user: IdentityUser,
        *,
        expected_version: int,
        fields: dict[str, str],
    ) -> None:
        if user.version != expected_version:
            raise AppError('version_conflict', '资料已被其他操作修改，请刷新后重试', 409)
        changed = await session.scalar(
            update(IdentityUser)
            .where(IdentityUser.id == user.id, IdentityUser.version == expected_version)
            .values(**fields, version=expected_version + 1, updated_at=datetime.now(timezone.utc))
            .returning(IdentityUser.id)
            .execution_options(synchronize_session=False)
        )
        if changed is None:
            raise AppError('version_conflict', '资料已被其他操作修改，请刷新后重试', 409)

    async def update_profile(self, session: AsyncSession, *, user_id: UUID, body: ProfilePatch) -> ProfileInfo:
        user = await self._profile_user(session, user_id, lock=True)
        await self._profile_cas(
            session,
            user,
            expected_version=body.expected_version,
            fields=body.model_dump(exclude_unset=True, exclude={'expected_version'}),
        )
        return await self.profile(session, user_id=user_id)

    async def change_password(self, session: AsyncSession, *, user_id: UUID, body: PasswordChange) -> None:
        user = await self._profile_user(session, user_id)
        if user.version != body.expected_version:
            raise AppError('version_conflict', '资料已被其他操作修改，请刷新后重试', 409)
        encoded = user.password_hash
        current_password = body.current_password.get_secret_value()
        new_password = body.new_password.get_secret_value()
        try:
            await asyncio.to_thread(self._hasher.verify, encoded, current_password)
        except (VerificationError, InvalidHashError) as exc:
            raise AppError('invalid_current_password', '当前密码错误', 400) from exc
        if new_password == current_password:
            raise AppError('unchanged_password', '新密码不能与当前密码相同', 400)
        # Both expensive Argon2 operations finish before taking the user lock.
        password_hash = await self.hash_password(new_password)
        user = await self._profile_user(session, user_id, lock=True)
        if user.password_hash != encoded:
            raise AppError('version_conflict', '登录凭据已被其他操作修改，请刷新后重试', 409)
        await self._profile_cas(
            session,
            user,
            expected_version=body.expected_version,
            fields={'password_hash': password_hash},
        )
        await session.execute(delete(IdentitySession).where(IdentitySession.user_id == user_id))

    async def related_users(self, session: AsyncSession, user_ids: tuple[UUID, ...]) -> list[IdentityDirectoryEntry]:
        """Minimal labels for already-authorized relationships; never a directory search."""
        if not user_ids:
            return []
        if len(user_ids) > MAX_RELATED_USERS:
            raise ValueError('Too many related identities')
        rows = await session.scalars(
            select(IdentityUser).where(IdentityUser.id.in_(user_ids), IdentityUser.deleted_at.is_(None))
        )
        return [IdentityDirectoryEntry.model_validate(row, from_attributes=True) for row in rows]

    async def directory(self, session: AsyncSession, *, search: str = '') -> list[IdentityInfo]:
        query = select(IdentityUser).where(IdentityUser.is_active.is_(True), IdentityUser.deleted_at.is_(None))
        if search:
            escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            query = query.where(IdentityUser.username.ilike(f'%{escaped}%', escape='\\'))
        return [
            IdentityInfo(id=row.id, username=row.username)
            for row in await session.scalars(query.order_by(IdentityUser.username).limit(100))
        ]

    async def directory_page(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        search: str = '',
        page: int = 1,
        page_size: int = 20,
    ) -> IdentityDirectoryPage:
        """Include inactive and never-used accounts without exposing profile or credential fields."""
        principal.require_any_permission('ai:credits:read', 'ai:credits:write')
        if page < 1 or not 1 <= page_size <= MAX_DIRECTORY_PAGE_SIZE:
            raise AppError('invalid_pagination', '分页参数无效', 422)
        if len(search) > MAX_DIRECTORY_SEARCH_LENGTH or '\x00' in search:
            raise AppError('invalid_identity_search', '用户搜索条件无效', 422)
        query = select(IdentityUser).where(IdentityUser.deleted_at.is_(None))
        if search:
            escaped = search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            pattern = f'%{escaped}%'
            query = query.where(
                or_(
                    IdentityUser.username.ilike(pattern, escape='\\'),
                    IdentityUser.display_name.ilike(pattern, escape='\\'),
                )
            )
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(IdentityUser.username, IdentityUser.id).offset((page - 1) * page_size).limit(page_size)
        )
        return IdentityDirectoryPage(
            items=[IdentityDirectoryEntry.model_validate(row, from_attributes=True) for row in rows],
            total=total or 0,
            page=page,
            page_size=page_size,
        )

    async def directory_user(
        self,
        session: AsyncSession,
        principal: Principal,
        user_id: UUID,
        *,
        require_active: bool = False,
        lock: bool = False,
    ) -> IdentityDirectoryEntry:
        principal.require_any_permission('ai:credits:read', 'ai:credits:write')
        query = select(IdentityUser).where(IdentityUser.id == user_id, IdentityUser.deleted_at.is_(None))
        if lock:
            query = query.with_for_update(read=True)
        user = await session.scalar(query.execution_options(populate_existing=True))
        if user is None:
            raise AppError('credit_user_not_found', '积分账户用户不存在', 404)
        if require_active and not user.is_active:
            raise AppError('credit_user_inactive', '用户已停用，不能继续发放积分', 409)
        return IdentityDirectoryEntry.model_validate(user, from_attributes=True)

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

    async def resource_owner(self, session: AsyncSession, user_id: UUID, *, lock: bool = False) -> ResourceOwnerInfo:
        """Internal relationship metadata; this does not grant directory access or execution rights."""
        query = select(IdentityUser.id, IdentityUser.username, IdentityUser.is_active, IdentityUser.deleted_at).where(
            IdentityUser.id == user_id
        )
        if lock:
            query = query.with_for_update(read=True)
        user = (await session.execute(query)).one_or_none()
        state = 'deleted' if user is None or user.deleted_at is not None else 'active' if user.is_active else 'disabled'
        return ResourceOwnerInfo(id=user_id, username=user.username if user else '', state=state)

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
                IdentityRole.system_key.is_(None),
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
        await sync_builtin_role_membership(session, user.id, is_admin=user.is_admin)
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
