"""System role metadata and memberships; administrator authority remains on the user."""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.identity.admin_orm import IdentityRole, IdentityUserRole

SUPER_ADMIN_KEY = 'super_admin'
SUPER_ADMIN_NAME = '超级管理员'
SUPER_ADMIN_REMARK = '系统内置角色，超级管理员身份由系统维护'


async def ensure_builtin_roles(session: AsyncSession) -> IdentityRole:
    """Create missing metadata without adopting an ordinary role or committing the caller's work."""
    suffix = 0
    while True:
        existing = await session.scalar(select(IdentityRole).where(IdentityRole.system_key == SUPER_ADMIN_KEY))
        if existing is not None:
            return existing
        code = 'super_admin' if suffix == 0 else 'system_super_admin' if suffix == 1 else f'system_super_admin_{suffix}'
        if await session.scalar(select(IdentityRole.id).where(IdentityRole.code == code)) is not None:
            suffix += 1
            continue
        role = IdentityRole(
            code=code,
            system_key=SUPER_ADMIN_KEY,
            name=SUPER_ADMIN_NAME,
            permissions=[],
            is_active=True,
            remark=SUPER_ADMIN_REMARK,
        )
        try:
            # The unique system key selects one winner when separate CLI/admin
            # transactions initialize an empty database concurrently.
            async with session.begin_nested():
                session.add(role)
                await session.flush()
        except IntegrityError:
            winner = await session.scalar(select(IdentityRole).where(IdentityRole.system_key == SUPER_ADMIN_KEY))
            if winner is not None:
                return winner
            if await session.scalar(select(IdentityRole.id).where(IdentityRole.code == code)) is None:
                raise
            suffix += 1
        else:
            return role


async def sync_builtin_role_membership(session: AsyncSession, user_id: UUID, *, is_admin: bool) -> None:
    """Apply the final identity flag, preserving every ordinary role assignment."""
    if not is_admin:
        await session.execute(
            delete(IdentityUserRole).where(
                IdentityUserRole.user_id == user_id,
                IdentityUserRole.role_id.in_(select(IdentityRole.id).where(IdentityRole.system_key == SUPER_ADMIN_KEY)),
            )
        )
        return
    role = await ensure_builtin_roles(session)
    if await session.get(IdentityUserRole, (user_id, role.id)) is not None:
        return
    try:
        async with session.begin_nested():
            session.add(IdentityUserRole(user_id=user_id, role_id=role.id))
            await session.flush()
    except IntegrityError:
        if await session.get(IdentityUserRole, (user_id, role.id)) is None:
            raise
