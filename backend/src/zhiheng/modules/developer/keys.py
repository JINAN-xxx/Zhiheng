from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select

from zhiheng.modules.developer.contracts import IssuedKey, KeyCreate, KeyInfo
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey, ResourceGrant
from zhiheng.modules.developer.scopes import (
    ResourceAction,
    ResourceKind,
    require_creation_capability,
    require_key_capabilities,
    resource_scope,
)
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability
from zhiheng.shared.references import ResourceReference
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    import builtins
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.billing.public import UsageFactsPort
    from zhiheng.modules.developer.applications import ApplicationService
    from zhiheng.modules.developer.scopes import Scope
    from zhiheng.modules.identity.public import IdentityService
    from zhiheng.modules.models.public import ModelService
    from zhiheng.shared.principal import Principal


@dataclass(frozen=True)
class DeveloperIdentity:
    application_id: UUID
    key_id: UUID
    owner_id: UUID


class KeyService:
    def __init__(
        self,
        applications: ApplicationService,
        models: ModelService,
        identity: IdentityService,
        facts: UsageFactsPort,
        agent_candidates: Callable[[AsyncSession, UUID, UUID], Awaitable[builtins.list[tuple[UUID, str]]]],
        mcp_candidates: Callable[[AsyncSession, UUID], Awaitable[set[UUID]]],
    ) -> None:
        self._applications, self._models, self._identity, self._facts = applications, models, identity, facts
        self._agent_candidates = agent_candidates
        self._mcp_candidates = mcp_candidates

    async def create(
        self, session: AsyncSession, principal: Principal, application_id: UUID, body: KeyCreate
    ) -> IssuedKey:
        app = await self._applications.require(session, principal, application_id, lock=True)
        if app.status != 'active' or not app.platform_enabled:
            raise AppError('application_conflict', '应用已停用', 409)
        require_key_capabilities(principal, body.scopes, operation='key_create')
        if body.issue_request_id is not None:
            issued = await session.scalar(
                select(DeveloperKey).where(
                    DeveloperKey.application_id == application_id,
                    DeveloperKey.issue_request_id == body.issue_request_id,
                )
            )
            if issued is not None:
                raise AppError(
                    'key_issue_already_processed',
                    '此签发请求已处理。请在列表核对原密钥。若未收到明文，请撤销后重新签发',
                    409,
                    details={'key_id': str(issued.id), 'name': issued.name},
                )
        for kind in body.create_resource_kinds:
            require_creation_capability(principal, kind)
        await self._models.bindings.resolve_many(session, body.model_ids)
        if body.agent_ids:
            candidates = await self._agent_candidates(session, principal.id, application_id)
            if not set(body.agent_ids).issubset(agent_id for agent_id, _ in candidates):
                raise AppError('agent_not_found', '请选择此应用已启用的智能体', 404)
        if body.mcp_tool_ids and not set(body.mcp_tool_ids).issubset(
            await self._mcp_candidates(session, application_id)
        ):
            raise AppError('mcp_publication_unavailable', '请选择此应用已发布且可用的 MCP 工具', 404)
        for selection in body.resource_grants:
            grant = await session.get(ResourceGrant, (application_id, selection.kind, selection.resource_id))
            if grant is None or not set(selection.actions).issubset(grant.actions):
                raise AppError('invalid_resource_grant', '所选资源或动作超出应用授权范围', 422)
        object_fields = {'agent_ids', 'model_ids', 'mcp_tool_ids', 'resource_grants', 'create_resource_kinds', 'scopes'}
        row = DeveloperKey(
            application_id=application_id,
            **body.model_dump(exclude=object_fields),
            **body.model_dump(mode='json', include=object_fields),
        )
        return await self._issue(session, row, principal, app)

    async def _issue(
        self, session: AsyncSession, row: DeveloperKey, principal: Principal, app: DeveloperApplication
    ) -> IssuedKey:
        secret = 'sk-zh-' + secrets.token_urlsafe(32)
        row.prefix = secret[:18]
        row.digest = hashlib.sha256(secret.encode()).hexdigest()
        session.add(row)
        await session.flush()
        return IssuedKey(**self.owner_info(row, principal, app).model_dump(), api_key=secret)

    async def list(
        self, session: AsyncSession, principal: Principal, application_id: UUID, page: int, page_size: int
    ) -> dict[str, object]:
        app = await self._applications.require(session, principal, application_id)
        query = select(DeveloperKey).where(
            DeveloperKey.application_id == application_id, DeveloperKey.deleted_at.is_(None)
        )
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(DeveloperKey.created_at.desc(), DeveloperKey.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {
            'items': [self.owner_info(row, principal, app) for row in rows],
            'total': total or 0,
            'page': page,
            'page_size': page_size,
        }

    async def require(
        self,
        session: AsyncSession,
        principal: Principal,
        application_id: UUID,
        key_id: UUID,
        *,
        lock: bool = False,
        include_deleted: bool = False,
    ) -> DeveloperKey:
        query = select(DeveloperKey).where(DeveloperKey.id == key_id, DeveloperKey.application_id == application_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await session.scalar(query.execution_options(populate_existing=True))
        # Global lock order is key -> application. Never acquire key while holding application.
        await self._applications.require(session, principal, application_id, lock=lock)
        if row is None or (row.deleted_at is not None and not include_deleted):
            raise AppError('key_not_found', '访问密钥不存在', 404)
        return row

    async def rotate(
        self, session: AsyncSession, principal: Principal, application_id: UUID, key_id: UUID, grace: int
    ) -> IssuedKey:
        row = await self.require(session, principal, application_id, key_id, lock=True)
        app = await session.get(DeveloperApplication, application_id)
        if app is None:
            raise AppError('key_conflict', '密钥不可轮换', 409)
        self._require_rotation(row, principal, app)
        row.status = 'revoked' if grace == 0 else 'retiring'
        row.retire_at = utcnow() + timedelta(seconds=grace) if grace > 0 else None
        replacement = DeveloperKey(
            application_id=application_id,
            grant_family_id=row.grant_family_id or row.id,
            **{field: getattr(row, field) for field in KeyCreate.model_fields if field != 'issue_request_id'},
        )
        return await self._issue(session, replacement, principal, app)

    @classmethod
    def _require_rotation(cls, row: DeveloperKey, principal: Principal, app: DeveloperApplication) -> None:
        if row.status != 'active' or not cls.usable(row) or app.status != 'active' or not app.platform_enabled:
            raise AppError('key_conflict', '密钥不可轮换', 409)
        require_key_capabilities(principal, tuple(row.scopes), operation='key_rotate')

    async def detail(self, session: AsyncSession, principal: Principal, application_id: UUID, key_id: UUID) -> KeyInfo:
        row = await self.require(session, principal, application_id, key_id)
        app = await session.get_one(DeveloperApplication, application_id)
        return self.owner_info(row, principal, app)

    async def revoke(self, session: AsyncSession, principal: Principal, application_id: UUID, key_id: UUID) -> KeyInfo:
        require_capability(principal, 'developer', 'key_revoke')
        row = await self.require(session, principal, application_id, key_id, lock=True)
        row.status = 'revoked'
        row.retire_at = None
        return self.info(row)

    async def delete(self, session: AsyncSession, principal: Principal, application_id: UUID, key_id: UUID) -> None:
        require_capability(principal, 'developer', 'key_delete')
        row = await self.require(session, principal, application_id, key_id, lock=True, include_deleted=True)
        if row.deleted_at is None:
            # Keep the original identity for accounting and accepted-work origins.
            # Revocation also protects executors loaded before this schema change.
            row.status = 'revoked'
            row.retire_at = None
            row.deleted_at = utcnow()

    async def authenticate(
        self, session: AsyncSession, token: str, *, lock: bool = False, scope: Scope | None = None
    ) -> DeveloperIdentity:
        query = select(DeveloperKey).where(DeveloperKey.digest == hashlib.sha256(token.encode()).hexdigest())
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        key = await session.scalar(query.execution_options(populate_existing=True))
        if key is None or not self.usable(key):
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        app_query = select(DeveloperApplication).where(DeveloperApplication.id == key.application_id)
        if lock:
            app_query = app_query.with_for_update().execution_options(populate_existing=True)
        app = await session.scalar(app_query.execution_options(populate_existing=True))
        if (
            app is None
            or app.deleted_at is not None
            or (app.status != 'active' or not app.platform_enabled)
            or not await self._identity.is_active(session, app.owner_id)
        ):
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        principal = await self._identity.principal_for_user(session, app.owner_id)
        require_key_capabilities(principal)
        if scope is not None:
            if scope not in key.scopes:
                raise AppError('insufficient_scope', 'Key lacks the required scope', 403)
            require_key_capabilities(principal, (scope,))
        return DeveloperIdentity(app.id, key.id, app.owner_id)

    async def revalidate(self, session: AsyncSession, identity: DeveloperIdentity, *, lock: bool = False) -> KeyInfo:
        """Current identity validity, independent of the operation's capability or token accounting."""
        return self.info(await self._current_row(session, identity, lock=lock))

    async def _current_row(
        self, session: AsyncSession, identity: DeveloperIdentity, *, lock: bool = False
    ) -> DeveloperKey:
        query = select(DeveloperKey).where(DeveloperKey.id == identity.key_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        key = await session.scalar(query.execution_options(populate_existing=True))
        app_query = select(DeveloperApplication).where(DeveloperApplication.id == identity.application_id)
        if lock:
            app_query = app_query.with_for_update().execution_options(populate_existing=True)
        app = await session.scalar(app_query.execution_options(populate_existing=True))
        if (
            key is None
            or not self.usable(key)
            or key.application_id != identity.application_id
            or app is None
            or app.owner_id != identity.owner_id
            or (app.status != 'active' or not app.platform_enabled)
            or app.deleted_at is not None
            or not await self._identity.is_active(session, identity.owner_id)
        ):
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        require_key_capabilities(await self._identity.principal_for_user(session, identity.owner_id))
        return key

    async def authorize_identity(
        self,
        session: AsyncSession,
        identity: DeveloperIdentity,
        model_ids: Sequence[UUID],
        *,
        scope: Scope,
        lock: bool = False,
    ) -> None:
        """Revalidate a persisted authenticated identity without retaining its bearer secret."""
        key = await self._scoped_key(session, identity, scope, lock=lock)
        self._require_models(key, model_ids)

    async def _scoped_key(
        self, session: AsyncSession, identity: DeveloperIdentity, scope: Scope, *, lock: bool
    ) -> DeveloperKey:
        key = await self._current_row(session, identity, lock=lock)
        if scope not in key.scopes:
            raise AppError('insufficient_scope', 'Key lacks the required scope', 403)
        require_key_capabilities(await self._identity.principal_for_user(session, identity.owner_id), (scope,))
        return key

    @staticmethod
    def _require_models(key: DeveloperKey, model_ids: Sequence[UUID]) -> None:
        if not {str(model_id) for model_id in model_ids}.issubset(key.model_ids):
            raise AppError('model_not_found', 'Model is unavailable', 404)

    async def authorize_agent(
        self,
        session: AsyncSession,
        identity: DeveloperIdentity,
        agent_id: UUID,
        *,
        lock: bool = False,
    ) -> None:
        key = await self._scoped_key(session, identity, 'agents:invoke', lock=lock)
        if str(agent_id) not in key.agent_ids:
            raise AppError('agent_not_found', 'Agent is unavailable', 404)

    @staticmethod
    def _selected_ids(key: DeveloperKey, kind: ResourceKind, action: ResourceAction) -> set[UUID]:
        return {
            grant.resource_id
            for grant in KeyService.info(key).resource_grants
            if grant.kind == kind and action in grant.actions
        }

    async def resource_ids(
        self, session: AsyncSession, identity: DeveloperIdentity, kind: ResourceKind, action: ResourceAction
    ) -> frozenset[UUID]:
        key = await self._current_row(session, identity)
        created = await session.scalars(
            select(ResourceGrant.resource_id).where(
                ResourceGrant.application_id == identity.application_id,
                ResourceGrant.kind == kind,
                ResourceGrant.creator_family_id == (key.grant_family_id or key.id),
                ResourceGrant.actions.contains([action]),
            )
        )
        return frozenset(self._selected_ids(key, kind, action)).union(created)

    async def authorize_resource(
        self,
        session: AsyncSession,
        identity: DeveloperIdentity,
        kind: ResourceKind,
        resource_id: UUID,
        action: ResourceAction,
        *,
        lock: bool = False,
    ) -> None:
        """Narrow the application/domain grant; the caller checks the endpoint's operation scope."""
        key = await self._current_row(session, identity, lock=lock)
        if resource_id in self._selected_ids(key, kind, action):
            return
        grant = await session.get(ResourceGrant, (identity.application_id, kind, resource_id), populate_existing=True)
        if grant is None or grant.creator_family_id != (key.grant_family_id or key.id) or action not in grant.actions:
            raise AppError('resource_not_found', '资源不存在', 404)

    async def authorize_resource_creation(
        self, session: AsyncSession, identity: DeveloperIdentity, kind: ResourceKind, *, lock: bool = False
    ) -> None:
        key = await self._scoped_key(session, identity, resource_scope(kind, 'write'), lock=lock)
        if kind not in key.create_resource_kinds:
            raise AppError('insufficient_scope', '密钥未获准创建此类资源', 403)

    async def grant_created_resource(
        self, session: AsyncSession, identity: DeveloperIdentity, kind: ResourceKind, resource_id: UUID
    ) -> None:
        key = await self._current_row(session, identity, lock=True)
        if kind not in key.create_resource_kinds:
            raise AppError('insufficient_scope', '密钥未获准创建此类资源', 403)
        grant = await session.get(ResourceGrant, (identity.application_id, kind, resource_id), with_for_update=True)
        if grant is None or grant.creator_family_id is not None:
            raise AppError('resource_conflict', '新建资源绑定已变化', 409)
        grant.creator_family_id = key.grant_family_id or key.id

    async def info_for_identity(self, session: AsyncSession, identity: DeveloperIdentity) -> KeyInfo:
        row = await session.get(DeveloperKey, identity.key_id, populate_existing=True)
        if row is None:
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        return self.info(row)

    async def authorize_run(
        self,
        session: AsyncSession,
        run_id: UUID,
        *,
        scope: Scope,
        agent_id: UUID | None = None,
        expected_identity: DeveloperIdentity | None = None,
    ) -> UUID:
        """Execution guard reads the original reservation identity, never a replaying Key."""
        invocation = await self._facts.invocation_identity(session, run_id)
        if invocation is None or invocation.key_id is None or invocation.application_id is None:
            raise AppError('invalid_api_key', 'Missing execution identity', 401)
        identity = DeveloperIdentity(invocation.application_id, invocation.key_id, invocation.user_id)
        if expected_identity is not None and identity != expected_identity:
            raise AppError('run_not_found', 'Run is unavailable', 404)
        if scope == 'agents:invoke':
            if agent_id is None:
                raise AppError('agent_not_found', 'Agent is unavailable', 404)
            await self.authorize_agent(session, identity, agent_id, lock=True)
        else:
            await self.authorize_identity(session, identity, [invocation.model_id], scope=scope, lock=True)
        return invocation.model_id

    async def references_model(self, session: AsyncSession, model_id: UUID) -> bool:
        return bool(await self.model_references(session, model_id))

    async def model_references(self, session: AsyncSession, model_id: UUID) -> builtins.list[ResourceReference]:
        rows = await session.execute(
            select(DeveloperKey, DeveloperApplication)
            .join(DeveloperApplication, DeveloperKey.application_id == DeveloperApplication.id)
            .where(
                DeveloperKey.model_ids.contains([str(model_id)]),
                DeveloperApplication.status == 'active',
                DeveloperApplication.deleted_at.is_(None),
            )
            .order_by(DeveloperKey.id)
        )
        return [self._reference(key, app) for key, app in rows if self.usable(key)]

    async def application_model_ids(self, session: AsyncSession, application_id: UUID) -> builtins.list[UUID]:
        rows = await session.scalars(select(DeveloperKey).where(DeveloperKey.application_id == application_id))
        return sorted({UUID(value) for key in rows if self.usable(key) for value in key.model_ids}, key=str)

    async def application_references(
        self, session: AsyncSession, application_id: UUID
    ) -> builtins.list[ResourceReference]:
        rows = await session.execute(
            select(DeveloperKey, DeveloperApplication)
            .join(DeveloperApplication, DeveloperKey.application_id == DeveloperApplication.id)
            .where(DeveloperApplication.id == application_id)
            .order_by(DeveloperKey.id)
        )
        return [self._reference(key, app) for key, app in rows if self.usable(key)]

    @staticmethod
    def _reference(key: DeveloperKey, application: DeveloperApplication) -> ResourceReference:
        return ResourceReference(
            'key',
            key.id,
            key.name,
            key.status,
            application.owner_id,
            f'/developer?applicationId={application.id}&tab=keys',
        )

    @staticmethod
    def usable(row: DeveloperKey) -> bool:
        now = utcnow()
        return (
            row.deleted_at is None
            and row.status != 'revoked'
            and (row.expires_at is None or row.expires_at > now)
            and (row.status != 'retiring' or (row.retire_at is not None and row.retire_at > now))
        )

    @classmethod
    def owner_info(cls, row: DeveloperKey, principal: Principal, app: DeveloperApplication) -> KeyInfo:
        try:
            cls._require_rotation(row, principal, app)
        except AppError:
            return cls.info(row)
        return cls.info(row, can_rotate=True)

    @classmethod
    def info(cls, row: DeveloperKey, *, can_rotate: bool = False) -> KeyInfo:
        status = row.status
        if status != 'revoked' and not cls.usable(row):
            status = 'expired'
        result = {name: getattr(row, name) for name in KeyInfo.model_fields if name != 'can_rotate'}
        return KeyInfo.model_validate({**result, 'status': status, 'can_rotate': can_rotate})
