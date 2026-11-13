from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import func, select

from zhiheng.modules.developer.contracts import IssuedKey, KeyCreate, KeyInfo
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability
from zhiheng.shared.references import ResourceReference
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    import builtins
    from collections.abc import Sequence

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
    ) -> None:
        self._applications, self._models, self._identity, self._facts = applications, models, identity, facts

    async def create(
        self, session: AsyncSession, principal: Principal, application_id: UUID, body: KeyCreate
    ) -> IssuedKey:
        app = await self._applications.require(session, principal, application_id, lock=True)
        if app.status != 'active' or not app.platform_enabled:
            raise AppError('application_conflict', '应用已停用', 409)
        self._require_modules(principal, body.scopes, invoke=False)
        await self._models.resolve_many(session, body.model_ids)
        return await self._issue(session, application_id, body)

    async def _issue(self, session: AsyncSession, application_id: UUID, body: KeyCreate) -> IssuedKey:
        secret = 'sk-zh-' + secrets.token_urlsafe(32)
        row = DeveloperKey(
            application_id=application_id,
            prefix=secret[:18],
            digest=hashlib.sha256(secret.encode()).hexdigest(),
            **body.model_dump(exclude={'model_ids', 'scopes'}),
            scopes=list(body.scopes),
            model_ids=[str(value) for value in body.model_ids],
        )
        session.add(row)
        await session.flush()
        return IssuedKey(**self.info(row).model_dump(), api_key=secret)

    async def list(
        self, session: AsyncSession, principal: Principal, application_id: UUID, page: int, page_size: int
    ) -> dict[str, object]:
        await self._applications.require(session, principal, application_id)
        query = select(DeveloperKey).where(
            DeveloperKey.application_id == application_id, DeveloperKey.deleted_at.is_(None)
        )
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(DeveloperKey.created_at.desc(), DeveloperKey.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {'items': [self.info(row) for row in rows], 'total': total or 0, 'page': page, 'page_size': page_size}

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
        if (
            row.status != 'active'
            or not self.usable(row)
            or app is None
            or (app.status != 'active' or not app.platform_enabled)
        ):
            raise AppError('key_conflict', '密钥不可轮换', 409)
        self._require_modules(principal, tuple(row.scopes), invoke=False, operation='key_rotate')
        row.status = 'revoked' if grace == 0 else 'retiring'
        row.retire_at = utcnow() + timedelta(seconds=grace) if grace > 0 else None
        body = KeyCreate(
            name=row.name,
            model_ids=[UUID(value) for value in row.model_ids],
            scopes=tuple(row.scopes),
            resource_rpm_limit=row.resource_rpm_limit,
            rpm_limit=row.rpm_limit,
            tpm_limit=row.tpm_limit,
            concurrency_limit=row.concurrency_limit,
            monthly_budget=str(row.monthly_budget) if row.monthly_budget is not None else None,
            monthly_points_limit=str(row.monthly_points_limit) if row.monthly_points_limit is not None else None,
            currency=row.currency,
            expires_at=row.expires_at,
        )
        return await self._issue(session, application_id, body)

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

    async def authenticate(self, session: AsyncSession, token: str, *, lock: bool = False) -> DeveloperIdentity:
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
        self._require_modules(await self._identity.principal_for_user(session, app.owner_id))
        return DeveloperIdentity(app.id, key.id, app.owner_id)

    async def revalidate(self, session: AsyncSession, identity: DeveloperIdentity, *, lock: bool = False) -> KeyInfo:
        """Current identity validity, independent of the operation's capability or token accounting."""
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
        self._require_modules(await self._identity.principal_for_user(session, identity.owner_id))
        return self.info(key)

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
        key = await self.revalidate(session, identity, lock=lock)
        if scope not in key.scopes:
            raise AppError('insufficient_scope', 'Key lacks the required scope', 403)
        self._require_modules(await self._identity.principal_for_user(session, identity.owner_id), (scope,))
        if not set(model_ids) <= set(key.model_ids):
            raise AppError('model_not_found', 'Model is unavailable', 404)

    @staticmethod
    def _require_modules(
        principal: Principal, scopes: Sequence[str] = (), *, invoke: bool = True, operation: str = 'key_create'
    ) -> None:
        require_capability(principal, 'developer', 'invoke' if invoke else operation)
        for scope in scopes:
            namespace, action = scope.split(':')
            module = {'knowledge': 'knowledge', 'skills': 'skills', 'agents': 'agents'}.get(namespace)
            if module is None:
                continue
            if action == 'write':
                operations = (
                    ('create', 'update', 'delete', 'import', 'rebuild')
                    if module == 'knowledge'
                    else ('create', 'update', 'delete', 'toggle')
                )
                principal.require_any_permission(*(f'platform:{module}:{operation}' for operation in operations))
            else:
                require_capability(principal, module, 'use' if action == 'invoke' else action)

    async def info_for_identity(self, session: AsyncSession, identity: DeveloperIdentity) -> KeyInfo:
        row = await session.get(DeveloperKey, identity.key_id, populate_existing=True)
        if row is None:
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        return self.info(row)

    async def authorize_run(self, session: AsyncSession, run_id: UUID, *, scope: Scope) -> None:
        """Execution guard reads the original reservation identity, never a replaying Key."""
        invocation = await self._facts.invocation_identity(session, run_id)
        if invocation is None or invocation.key_id is None or invocation.application_id is None:
            raise AppError('invalid_api_key', 'Missing execution identity', 401)
        identity = DeveloperIdentity(invocation.application_id, invocation.key_id, invocation.user_id)
        await self.authorize_identity(session, identity, [invocation.model_id], scope=scope, lock=True)

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
    def info(cls, row: DeveloperKey) -> KeyInfo:
        status = row.status
        if status != 'revoked' and not cls.usable(row):
            status = 'expired'
        return KeyInfo.model_validate({**{name: getattr(row, name) for name in KeyInfo.model_fields}, 'status': status})
