"""Connection credentials, immutable revisions and administrative lifecycle."""

from collections.abc import Awaitable, Callable
from secrets import compare_digest
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.modules.models.contracts import (
    ConnectionInfo,
    ConnectionOption,
)
from zhiheng.modules.models.orm import ConnectionRevision, ModelConnection, ModelDefinition
from zhiheng.modules.models.projections import connection_info
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import ResourceReference, reference_conflict
from zhiheng.shared.time import utcnow


class ModelConnections:
    def __init__(self, cipher: CredentialCipher) -> None:
        self._cipher = cipher

    async def create(
        self, session: AsyncSession, principal: Principal, *, name: str, base_url: str, api_key: str
    ) -> ConnectionInfo:
        principal.require_permission('ai:credentials:create')
        connection = ModelConnection(
            name=name, base_url=base_url.rstrip('/'), encrypted_api_key=self._cipher.encrypt(api_key)
        )
        session.add(connection)
        await session.flush()
        await self._revision(session, connection)
        return connection_info(connection)

    async def _revision(self, session: AsyncSession, row: ModelConnection) -> ConnectionRevision:
        revision = ConnectionRevision(
            connection_id=row.id, version=row.revision, base_url=row.base_url, encrypted_api_key=row.encrypted_api_key
        )
        session.add(revision)
        await session.flush()
        return revision

    async def options(self, session: AsyncSession, principal: Principal) -> list[ConnectionOption]:
        principal.require_any_permission(
            'platform:models:config_read',
            'platform:models:create',
            'platform:models:update',
            'platform:models:delete',
            'platform:models:toggle',
        )
        rows = await session.scalars(
            select(ModelConnection)
            .where(ModelConnection.deleted_at.is_(None))
            .order_by(ModelConnection.name)
            .limit(100)
        )
        return [ConnectionOption(id=row.id, name=row.name, enabled=row.enabled) for row in rows]

    async def update(
        self, session: AsyncSession, principal: Principal, connection_id: UUID, fields: dict[str, object]
    ) -> ConnectionInfo:
        principal.require_permission('ai:credentials:toggle' if set(fields) == {'enabled'} else 'ai:credentials:update')
        if 'enabled' in fields and set(fields) != {'enabled'}:
            principal.require_permission('ai:credentials:toggle')
        row = await session.scalar(
            select(ModelConnection)
            .where(ModelConnection.id == connection_id, ModelConnection.deleted_at.is_(None))
            .with_for_update()
        )
        if row is None:
            raise AppError('connection_not_found', '模型连接不存在', 404)
        base_url = str(fields.get('base_url', row.base_url)).rstrip('/')
        address_changed = base_url != row.base_url
        encrypted_api_key = row.encrypted_api_key
        if 'api_key' in fields:
            api_key = str(fields['api_key'])
            if not api_key.strip():
                raise AppError('invalid_connection_key', '请输入有效的 API 密钥', 422)
            try:
                unchanged = compare_digest(
                    api_key.strip().encode(), self._cipher.decrypt(encrypted_api_key).strip().encode()
                )
            except AppError as error:
                if error.code != 'credential_unavailable':
                    raise
                unchanged = False
            if not unchanged:
                encrypted_api_key = self._cipher.encrypt(api_key)
        credential_changed = encrypted_api_key != row.encrypted_api_key
        if address_changed and not credential_changed:
            raise AppError('connection_new_key_required', '更改 API 地址时，请提供与当前密钥不同的新 API 密钥', 422)
        if 'name' in fields:
            row.name = str(fields['name'])
        row.base_url, row.encrypted_api_key = base_url, encrypted_api_key
        if 'enabled' in fields:
            row.enabled = bool(fields['enabled'])
        if address_changed or credential_changed:
            row.revision += 1
            await self._revision(session, row)
        return connection_info(row)

    async def remove(
        self,
        session: AsyncSession,
        principal: Principal,
        connection_id: UUID,
        *,
        reference_check: Callable[[AsyncSession, UUID], Awaitable[bool]],
    ) -> ConnectionInfo:
        principal.require_permission('ai:credentials:delete')
        row = await session.scalar(
            select(ModelConnection)
            .where(ModelConnection.id == connection_id, ModelConnection.deleted_at.is_(None))
            .with_for_update()
        )
        if row is None:
            raise AppError('connection_not_found', '模型连接不存在', 404)
        # Tombstones retain identities and credential revisions for audit and cleanup.
        models = await session.scalars(
            select(ModelDefinition)
            .where(ModelDefinition.connection_id == connection_id, ModelDefinition.deleted_at.is_(None))
            .order_by(ModelDefinition.id)
        )
        references = [
            ResourceReference('model', model.id, model.name, 'enabled' if model.enabled else 'disabled', path='/models')
            for model in models
        ]
        if references:
            raise reference_conflict('connection_referenced', '连接仍被当前模型引用', principal, references)
        if await reference_check(session, connection_id):
            raise AppError('connection_referenced', '连接仍被进行中的任务引用', 409)
        row.deleted_at, row.enabled = utcnow(), False
        return connection_info(row)

    async def list(self, session: AsyncSession, principal: Principal) -> list[ConnectionInfo]:
        principal.require_permission('ai:credentials:read')
        rows = await session.scalars(
            select(ModelConnection)
            .where(ModelConnection.deleted_at.is_(None))
            .order_by(ModelConnection.name)
            .limit(100)
        )
        counts_result = await session.execute(
            select(ModelDefinition.connection_id, func.count())
            .where(ModelDefinition.deleted_at.is_(None))
            .group_by(ModelDefinition.connection_id)
        )
        counts: dict[UUID, int] = dict(counts_result.tuples().all())
        return [connection_info(row).model_copy(update={'model_count': counts.get(row.id, 0)}) for row in rows]
