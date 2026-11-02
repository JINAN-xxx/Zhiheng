"""Account creation composes identity data with personal resource initialization."""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.identity.public import IdentityAdminService, IdentityService
from zhiheng.modules.skills.public import SkillService
from zhiheng.shared.principal import Principal


class AccountService:
    def __init__(
        self, identity: IdentityService, skills: SkillService, admin: IdentityAdminService | None = None
    ) -> None:
        self.identity, self.skills, self.admin = identity, skills, admin

    async def create_user(
        self,
        session: AsyncSession,
        *,
        username: str,
        password: str,
        is_admin: bool = False,
    ) -> Principal:
        user = await self.identity.create_user(session, username=username, password=password, is_admin=is_admin)
        await self.skills.initialize_owner(session, user.id)
        return user

    async def create(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        assert self.admin is not None
        result = await self.admin.create(session, principal, kind, fields)
        if kind == 'users':
            await self.skills.initialize_owner(session, UUID(str(result['id'])))
        return result
