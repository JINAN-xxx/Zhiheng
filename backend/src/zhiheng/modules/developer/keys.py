import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.applications import ApplicationService
from zhiheng.modules.developer.contracts import IssuedKey, KeyCreate, KeyInfo
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.models.public import ModelService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


@dataclass(frozen=True)
class DeveloperIdentity:
    application_id: UUID
    key_id: UUID
    owner_id: UUID


class KeyService:
    def __init__(self, applications: ApplicationService, models: ModelService, identity: IdentityService) -> None:
        self._applications, self._models, self._identity = applications, models, identity

    async def create(
        self, session: AsyncSession, principal: Principal, application_id: UUID, body: KeyCreate
    ) -> IssuedKey:
        app = await self._applications.require(session, principal, application_id, lock=True)
        if app.status != 'active':
            raise AppError('application_conflict', '应用已停用', 409)
        for model_id in sorted(body.model_ids, key=str):
            await self._models.resolve(session, model_id)
        return await self._issue(session, application_id, body)

    async def _issue(self, session: AsyncSession, application_id: UUID, body: KeyCreate) -> IssuedKey:
        secret = 'sk-zh-' + secrets.token_urlsafe(32)
        row = DeveloperKey(
            application_id=application_id,
            prefix=secret[:18],
            digest=hashlib.sha256(secret.encode()).hexdigest(),
            **body.model_dump(exclude={'model_ids'}),
            model_ids=[str(value) for value in body.model_ids],
        )
        session.add(row)
        await session.flush()
        return IssuedKey(**self.info(row).model_dump(), api_key=secret)

    async def list(
        self, session: AsyncSession, principal: Principal, application_id: UUID, page: int, page_size: int
    ) -> dict[str, object]:
        await self._applications.require(session, principal, application_id)
        query = select(DeveloperKey).where(DeveloperKey.application_id == application_id)
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(DeveloperKey.created_at.desc(), DeveloperKey.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {'items': [self.info(row) for row in rows], 'total': total or 0, 'page': page, 'page_size': page_size}

    async def require(
        self, session: AsyncSession, principal: Principal, application_id: UUID, key_id: UUID, *, lock: bool = False
    ) -> DeveloperKey:
        query = select(DeveloperKey).where(DeveloperKey.id == key_id, DeveloperKey.application_id == application_id)
        if lock:
            query = query.with_for_update()
        row = await session.scalar(query)
        # Global lock order is key -> application. Never acquire key while holding application.
        await self._applications.require(session, principal, application_id, lock=lock)
        if row is None:
            raise AppError('key_not_found', '访问密钥不存在', 404)
        return row

    async def rotate(
        self, session: AsyncSession, principal: Principal, application_id: UUID, key_id: UUID, grace: int
    ) -> IssuedKey:
        row = await self.require(session, principal, application_id, key_id, lock=True)
        app = await session.get(DeveloperApplication, application_id)
        if row.status != 'active' or not self.usable(row) or app is None or app.status != 'active':
            raise AppError('key_conflict', '密钥不可轮换', 409)
        row.status = 'revoked' if grace == 0 else 'retiring'
        row.retire_at = utcnow() + timedelta(seconds=grace) if grace > 0 else None
        body = KeyCreate(
            name=row.name,
            model_ids=[UUID(value) for value in row.model_ids],
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
        row = await self.require(session, principal, application_id, key_id, lock=True)
        row.status = 'revoked'
        row.retire_at = None
        return self.info(row)

    async def authenticate(self, session: AsyncSession, token: str, *, lock: bool = False) -> DeveloperIdentity:
        query = select(DeveloperKey).where(DeveloperKey.digest == hashlib.sha256(token.encode()).hexdigest())
        if lock:
            query = query.with_for_update()
        key = await session.scalar(query)
        if key is None or not self.usable(key):
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        app_query = select(DeveloperApplication).where(DeveloperApplication.id == key.application_id)
        if lock:
            app_query = app_query.with_for_update()
        app = await session.scalar(app_query)
        if (
            app is None
            or app.deleted_at is not None
            or app.status != 'active'
            or not await self._identity.is_active(session, app.owner_id)
        ):
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        return DeveloperIdentity(app.id, key.id, app.owner_id)

    async def authorize_identity(
        self, session: AsyncSession, identity: DeveloperIdentity, model_ids: Sequence[UUID], *, lock: bool = False
    ) -> None:
        """Revalidate a persisted authenticated identity without retaining its bearer secret."""
        query = select(DeveloperKey).where(DeveloperKey.id == identity.key_id)
        if lock:
            query = query.with_for_update()
        key = await session.scalar(query)
        app_query = select(DeveloperApplication).where(DeveloperApplication.id == identity.application_id)
        if lock:
            app_query = app_query.with_for_update()
        app = await session.scalar(app_query)
        if (
            key is None
            or not self.usable(key)
            or key.application_id != identity.application_id
            or app is None
            or app.owner_id != identity.owner_id
            or app.status != 'active'
            or app.deleted_at is not None
            or not await self._identity.is_active(session, identity.owner_id)
        ):
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        if not {str(value) for value in model_ids} <= set(key.model_ids):
            raise AppError('model_not_found', 'Model is unavailable', 404)

    async def info_for_identity(self, session: AsyncSession, identity: DeveloperIdentity) -> KeyInfo:
        row = await session.get(DeveloperKey, identity.key_id)
        if row is None:
            raise AppError('invalid_api_key', 'Invalid API key', 401)
        return self.info(row)

    async def references_model(self, session: AsyncSession, model_id: UUID) -> bool:
        return (
            await session.scalar(
                select(DeveloperKey.id).where(DeveloperKey.model_ids.contains([str(model_id)])).limit(1)
            )
            is not None
        )

    @staticmethod
    def usable(row: DeveloperKey) -> bool:
        now = utcnow()
        return (
            row.status != 'revoked'
            and (row.expires_at is None or row.expires_at > now)
            and (row.status != 'retiring' or (row.retire_at is not None and row.retire_at > now))
        )

    @classmethod
    def info(cls, row: DeveloperKey) -> KeyInfo:
        status = row.status
        if status != 'revoked' and not cls.usable(row):
            status = 'expired'
        return KeyInfo.model_validate({**{name: getattr(row, name) for name in KeyInfo.model_fields}, 'status': status})
