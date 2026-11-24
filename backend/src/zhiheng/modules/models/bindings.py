"""Frozen model bindings and supplier construction; cleanup grants no invocation authority."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.modules.models.contracts import (
    ModelBinding,
    ModelProvider,
    ProviderFactory,
)
from zhiheng.modules.models.orm import ConnectionRevision, ModelConnection, ModelDefinition
from zhiheng.modules.models.projections import binding_for, model_info
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

DAIP_TOKEN_PLAN_URL = 'https://zhenze-huhehaote.cmecloud.cn/tokenplan/v1'


class ModelBindings:
    def __init__(self, cipher: CredentialCipher, timeout_seconds: float, *, provider_factory: ProviderFactory) -> None:
        self._cipher, self._timeout, self._provider_factory = cipher, timeout_seconds, provider_factory

    async def lock_many(self, session: AsyncSession, model_ids: list[UUID]) -> None:
        """Order dependency locks without granting liveness or invocation authority."""
        models = list(
            await session.scalars(
                select(ModelDefinition)
                .where(ModelDefinition.id.in_(model_ids))
                .order_by(ModelDefinition.id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )
        await session.execute(
            select(ModelConnection)
            .where(ModelConnection.id.in_({item.connection_id for item in models}))
            .order_by(ModelConnection.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )

    async def resolve_many(self, session: AsyncSession, model_ids: list[UUID]) -> dict[UUID, ModelBinding]:
        """Freeze binding metadata while allowing invocation foreign-key checks."""
        models = list(
            await session.scalars(
                select(ModelDefinition)
                .where(ModelDefinition.id.in_(model_ids))
                .order_by(ModelDefinition.id)
                .with_for_update(read=True)
            )
        )
        if len(models) != len(set(model_ids)) or any(
            not item.enabled or item.deleted_at is not None for item in models
        ):
            raise AppError('model_unavailable', '模型不存在或不可用', 404)
        await session.execute(
            select(ModelConnection)
            .where(ModelConnection.id.in_({item.connection_id for item in models}))
            .order_by(ModelConnection.id)
            .with_for_update(read=True)
        )
        return {item.id: await self.resolve(session, item.id, lock=False) for item in models}

    async def resolve(self, session: AsyncSession, model_id: UUID, *, lock: bool = True) -> ModelBinding:
        return await self._resolve(session, model_id, lock=lock)

    async def resolve_for_test(
        self, session: AsyncSession, principal: Principal, model_id: UUID, *, lock: bool = True
    ) -> ModelBinding:
        principal.require_permission('platform:models:test')
        return await self._resolve(session, model_id, lock=lock)

    async def _resolve(self, session: AsyncSession, model_id: UUID, *, lock: bool) -> ModelBinding:
        model_query = select(ModelDefinition).where(ModelDefinition.id == model_id)
        if lock:
            # SHARE permits concurrent binding reads and settlement's FK checks,
            # while excluding configuration updates and deletion until commit.
            model_query = model_query.with_for_update(read=True)
        model = await session.scalar(model_query)
        if model is None or not model.enabled or model.deleted_at is not None:
            raise AppError('model_unavailable', '模型不存在或不可用', 404)
        connection_query = select(ModelConnection).where(ModelConnection.id == model.connection_id)
        if lock:
            connection_query = connection_query.with_for_update(read=True)
        connection = await session.scalar(connection_query)
        if connection is None or not connection.enabled or connection.deleted_at is not None:
            raise AppError('model_unavailable', '模型不存在或不可用', 404)
        revision = await session.scalar(
            select(ConnectionRevision).where(
                ConnectionRevision.connection_id == connection.id, ConnectionRevision.version == connection.revision
            )
        )
        if revision is None:
            raise AppError('connection_unavailable', '模型连接版本不可用', 503)
        return binding_for(model_info(model), model_id=model.id, revision_id=revision.id)

    async def supports_low_reasoning(self, session: AsyncSession, binding: ModelBinding) -> bool:
        if (
            binding.model_type != 'chat'
            or binding.upstream_model.casefold() != 'glm-5.3'
            or binding.connection_revision_id is None
        ):
            return False
        revision = await session.get(ConnectionRevision, binding.connection_revision_id)
        return bool(
            revision is not None
            and revision.connection_id == binding.connection_id
            and revision.base_url.rstrip('/') == DAIP_TOKEN_PLAN_URL
        )

    async def provider(self, session: AsyncSession, binding: ModelBinding) -> ModelProvider:
        connection: ModelConnection | ConnectionRevision | None
        if binding.connection_revision_id is None:
            connection = await session.get(ModelConnection, binding.connection_id)
        else:
            connection = await session.get(ConnectionRevision, binding.connection_revision_id)
        if connection is None:
            raise AppError('connection_unavailable', '模型连接不可用', 503)
        return self._provider_factory(
            base_url=connection.base_url,
            api_key=self._cipher.decrypt(connection.encrypted_api_key),
            binding=binding,
            timeout_seconds=self._timeout,
        )

    async def retained(self, session: AsyncSession, model_id: UUID) -> ModelBinding:
        """Metadata for owned resource cleanup; it does not authorize a model invocation."""
        model = await session.scalar(select(ModelDefinition).where(ModelDefinition.id == model_id).with_for_update())
        if model is None:
            raise AppError('model_not_found', '模型元数据不存在', 404)
        connection = await session.scalar(
            select(ModelConnection).where(ModelConnection.id == model.connection_id).with_for_update()
        )
        if connection is None:
            raise AppError('connection_unavailable', '模型连接元数据不存在', 503)
        revision = await session.scalar(
            select(ConnectionRevision).where(
                ConnectionRevision.connection_id == connection.id, ConnectionRevision.version == connection.revision
            )
        )
        if revision is None:
            raise AppError('connection_unavailable', '模型连接版本不存在', 503)
        return binding_for(model_info(model), model_id=model.id, revision_id=revision.id)
