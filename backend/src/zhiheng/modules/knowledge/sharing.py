"""Authenticated, single-recipient invitations without exposing an account directory."""

import base64
import hashlib
import hmac
import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.files.public import AclEntry
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.contracts import BaseAcl, BaseAclInfo
from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeGrant, KnowledgeInvitation
from zhiheng.modules.knowledge.service import KnowledgeService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

MAX_SHARING_MEMBERS = 100


class InvitationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_acl_version: int = Field(strict=True, ge=1)
    client_request_id: UUID


class InvitationToken(BaseModel):
    model_config = ConfigDict(extra='forbid')
    token: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')


class InvitationInfo(BaseModel):
    id: UUID
    status: Literal['active', 'used', 'expired', 'revoked']
    expires_at: datetime
    created_at: datetime
    accepted_at: datetime | None


class CreatedInvitation(BaseModel):
    invitation: InvitationInfo
    token: str


class SharingMember(BaseModel):
    id: UUID
    name: str
    active: bool


class InvitationPreview(BaseModel):
    base_id: UUID
    base_name: str
    owner_name: str
    expires_at: datetime
    already_member: bool


class InvitationAccepted(BaseModel):
    base_id: UUID
    already_member: bool


class MemberRemoval(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_acl_version: int = Field(strict=True, ge=1)


class KnowledgeSharing:
    def __init__(self, knowledge: KnowledgeService, identity: IdentityService, token_key: bytes) -> None:
        self.knowledge, self.identity, self._token_key = knowledge, identity, token_key

    async def _owner(self, session: AsyncSession, principal: Principal, base_id: UUID) -> KnowledgeBase:
        base = await self.knowledge.require(session, principal, base_id, manage=True, lock=True)
        if base.owner_id != principal.id:
            raise AppError('knowledge_not_found', '知识库不存在', 404)
        return base

    def _token(self, base_id: UUID, owner_id: UUID, request_id: UUID) -> str:
        value = hmac.digest(
            self._token_key, b'knowledge-invitation-v1' + base_id.bytes + owner_id.bytes + request_id.bytes, 'sha256'
        )
        return base64.urlsafe_b64encode(value).decode().rstrip('=')

    @staticmethod
    def _info(base: KnowledgeBase, row: KnowledgeInvitation) -> InvitationInfo:
        status: Literal['active', 'used', 'expired', 'revoked'] = (
            'used'
            if row.accepted_at
            else 'revoked'
            if row.revoked_at or row.epoch != base.invite_epoch
            else 'expired'
            if row.expires_at <= utcnow()
            else 'active'
        )
        return InvitationInfo(
            id=row.id, status=status, expires_at=row.expires_at, created_at=row.created_at, accepted_at=row.accepted_at
        )

    async def create(
        self, session: AsyncSession, principal: Principal, base_id: UUID, body: InvitationCreate
    ) -> CreatedInvitation:
        base = await self._owner(session, principal, base_id)
        if base.document_policy != 'library':
            raise AppError('knowledge_migration_required', '请先迁移资料与共享权限，再生成邀请', 409)
        row = await session.scalar(
            select(KnowledgeInvitation).where(
                KnowledgeInvitation.base_id == base_id, KnowledgeInvitation.request_id == body.client_request_id
            )
        )
        token = self._token(base_id, principal.id, body.client_request_id)
        digest = hashlib.sha256(token.encode()).hexdigest()
        if row is not None:
            if row.owner_id != principal.id or not hmac.compare_digest(row.token_hash, digest):
                raise AppError('invitation_changed', '原邀请无法重新显示，请撤销后重新生成', 409)
            return CreatedInvitation(invitation=self._info(base, row), token=token)
        self.knowledge.version(base.acl_version, body.expected_acl_version)
        row = KnowledgeInvitation(
            base_id=base_id,
            owner_id=principal.id,
            request_id=body.client_request_id,
            token_hash=digest,
            epoch=base.invite_epoch,
            expires_at=utcnow() + timedelta(hours=24),
        )
        session.add(row)
        await session.flush()
        return CreatedInvitation(invitation=self._info(base, row), token=token)

    async def invitations(self, session: AsyncSession, principal: Principal, base_id: UUID) -> list[InvitationInfo]:
        base = await self._owner(session, principal, base_id)
        rows = await session.scalars(
            select(KnowledgeInvitation)
            .where(KnowledgeInvitation.base_id == base_id)
            .order_by(KnowledgeInvitation.created_at.desc())
            .limit(50)
        )
        return [self._info(base, row) for row in rows]

    async def revoke(
        self, session: AsyncSession, principal: Principal, base_id: UUID, invitation_id: UUID
    ) -> InvitationInfo:
        base = await self._owner(session, principal, base_id)
        row = await session.scalar(
            select(KnowledgeInvitation)
            .where(KnowledgeInvitation.id == invitation_id, KnowledgeInvitation.base_id == base_id)
            .with_for_update()
        )
        if row is None:
            raise AppError('invitation_not_found', '邀请不存在', 404)
        row.revoked_at = row.revoked_at or utcnow()
        await session.flush()
        return self._info(base, row)

    async def link(
        self, session: AsyncSession, principal: Principal, base_id: UUID, invitation_id: UUID
    ) -> CreatedInvitation:
        base = await self._owner(session, principal, base_id)
        row = await session.scalar(
            select(KnowledgeInvitation).where(
                KnowledgeInvitation.id == invitation_id, KnowledgeInvitation.base_id == base_id
            )
        )
        if row is None or self._info(base, row).status != 'active':
            raise AppError('invitation_unavailable', '邀请已失效，请重新生成', 404)
        token = self._token(base_id, principal.id, row.request_id)
        if not hmac.compare_digest(row.token_hash, hashlib.sha256(token.encode()).hexdigest()):
            raise AppError('invitation_changed', '原邀请无法重新显示，请撤销后重新生成', 409)
        return CreatedInvitation(invitation=self._info(base, row), token=token)

    async def cleanup_legacy(
        self, session: AsyncSession, principal: Principal, base_id: UUID, body: MemberRemoval
    ) -> BaseAclInfo:
        base = await self._owner(session, principal, base_id)
        self.knowledge.version(base.acl_version, body.expected_acl_version)
        grants = list(await session.scalars(select(KnowledgeGrant).where(KnowledgeGrant.base_id == base_id)))
        if not any(grant.effect == 'deny' or grant.expires_at is not None for grant in grants):
            return await self.knowledge.acl(session, principal, base_id)
        # Cleanup only removes old restrictions/temporary memberships; it never creates a grant.
        kept = [
            AclEntry(user_id=grant.user_id, effect='allow')
            for grant in grants
            if grant.effect == 'allow' and grant.expires_at is None
        ]
        return await self.knowledge.set_acl(
            session,
            principal,
            base_id,
            BaseAcl(expected_version=body.expected_acl_version, access='private', entries=kept),
        )

    async def members(self, session: AsyncSession, principal: Principal, base_id: UUID) -> list[SharingMember]:
        base = await self._owner(session, principal, base_id)
        identifiers = tuple(
            await session.scalars(
                select(KnowledgeGrant.user_id)
                .where(
                    KnowledgeGrant.base_id == base_id,
                    KnowledgeGrant.effect == 'allow',
                    KnowledgeGrant.user_id != base.owner_id,
                )
                .limit(MAX_SHARING_MEMBERS)
            )
        )
        labels = {user.id: user for user in await self.identity.related_users(session, identifiers)}
        return [
            SharingMember(
                id=identifier,
                name=(labels[identifier].display_name or labels[identifier].username)
                if identifier in labels
                else '已注销成员',
                active=identifier in labels and labels[identifier].is_active,
            )
            for identifier in identifiers
        ]

    async def remove_member(
        self, session: AsyncSession, principal: Principal, base_id: UUID, user_id: UUID, body: MemberRemoval
    ) -> None:
        base = await self._owner(session, principal, base_id)
        self.knowledge.version(base.acl_version, body.expected_acl_version)
        if user_id == base.owner_id:
            raise AppError('knowledge_owner_member', '不能移除知识库所有者', 409)
        await session.execute(
            delete(KnowledgeGrant).where(KnowledgeGrant.base_id == base_id, KnowledgeGrant.user_id == user_id)
        )
        base.acl_version += 1
        base.invite_epoch += 1
        base.updated_at = utcnow()
        await session.flush()

    async def _invitation(self, session: AsyncSession, token: str) -> tuple[KnowledgeBase, KnowledgeInvitation]:
        missing = AppError('invitation_unavailable', '邀请无效、已过期、已使用或已撤销', 404)
        if re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is None:
            raise missing
        digest = hashlib.sha256(token.encode()).hexdigest()
        base_id = await session.scalar(
            select(KnowledgeInvitation.base_id).where(KnowledgeInvitation.token_hash == digest)
        )
        if base_id is None:
            raise missing
        base = await session.scalar(select(KnowledgeBase).where(KnowledgeBase.id == base_id).with_for_update())
        row = await session.scalar(
            select(KnowledgeInvitation).where(KnowledgeInvitation.token_hash == digest).with_for_update()
        )
        if (
            base is None
            or row is None
            or row.owner_id != base.owner_id
            or base.deleted_at is not None
            or base.document_policy != 'library'
            or base.access != 'private'
            or row.revoked_at is not None
            or row.epoch != base.invite_epoch
            or row.expires_at <= utcnow()
            or not await self.identity.is_active(session, base.owner_id)
        ):
            raise missing
        return base, row

    async def _already_member(self, session: AsyncSession, principal: Principal, base: KnowledgeBase) -> bool:
        if base.owner_id == principal.id:
            return True
        grant = await session.get(KnowledgeGrant, (base.id, principal.id))
        if grant is not None and grant.effect == 'deny':
            raise AppError('invitation_unavailable', '邀请无效、已过期、已使用或已撤销', 404)
        return grant is not None and (grant.expires_at is None or grant.expires_at > utcnow())

    async def preview(self, session: AsyncSession, principal: Principal, token: str) -> InvitationPreview:
        base, row = await self._invitation(session, token)
        already = await self._already_member(session, principal, base)
        if row.accepted_by is not None and (row.accepted_by != principal.id or not already):
            raise AppError('invitation_unavailable', '邀请无效、已过期、已使用或已撤销', 404)
        owners = await self.identity.related_users(session, (base.owner_id,))
        return InvitationPreview(
            base_id=base.id,
            base_name=base.name,
            owner_name=(owners[0].display_name or owners[0].username) if owners else '知识库所有者',
            expires_at=row.expires_at,
            already_member=already,
        )

    async def accept(self, session: AsyncSession, principal: Principal, token: str) -> InvitationAccepted:
        base, row = await self._invitation(session, token)
        already = await self._already_member(session, principal, base)
        if row.accepted_by is not None:
            if row.accepted_by == principal.id and already:
                return InvitationAccepted(base_id=base.id, already_member=True)
            raise AppError('invitation_unavailable', '邀请无效、已过期、已使用或已撤销', 404)
        if already:
            return InvitationAccepted(base_id=base.id, already_member=True)
        count = await session.scalar(
            select(func.count()).select_from(KnowledgeGrant).where(KnowledgeGrant.base_id == base.id)
        )
        grant = await session.get(KnowledgeGrant, (base.id, principal.id))
        if grant is None and count is not None and count >= MAX_SHARING_MEMBERS:
            raise AppError('knowledge_member_limit', '共享成员已达上限，请联系所有者处理', 409)
        if grant is None:
            session.add(KnowledgeGrant(base_id=base.id, user_id=principal.id, effect='allow'))
        else:
            grant.expires_at = None
        row.accepted_by, row.accepted_at = principal.id, utcnow()
        base.acl_version += 1
        base.updated_at = utcnow()
        await session.flush()
        return InvitationAccepted(base_id=base.id, already_member=False)
