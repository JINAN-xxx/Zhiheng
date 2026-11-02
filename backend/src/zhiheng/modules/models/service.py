from collections.abc import Awaitable, Callable
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
from zhiheng.modules.models.adapters.structured import StructuredProvider
from zhiheng.modules.models.contracts import (
    ConnectionInfo,
    ModelBinding,
    ModelInfo,
    ModelProvider,
    RateCreate,
    RateInfo,
)
from zhiheng.modules.models.orm import ConnectionRevision, ModelConnection, ModelDefinition, RateCard
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import ResourceReference, reference_conflict
from zhiheng.shared.time import utcnow


class ModelService:
    def __init__(self, cipher: CredentialCipher, timeout_seconds: float) -> None:
        self._cipher = cipher
        self._timeout = timeout_seconds

    async def create_connection(
        self, session: AsyncSession, principal: Principal, *, name: str, base_url: str, api_key: str
    ) -> ConnectionInfo:
        principal.require_permission('ai:models:write')
        connection = ModelConnection(
            name=name, base_url=base_url.rstrip('/'), encrypted_api_key=self._cipher.encrypt(api_key)
        )
        session.add(connection)
        await session.flush()
        await self._revision(session, connection)
        return self._connection_info(connection)

    async def _revision(self, session: AsyncSession, row: ModelConnection) -> ConnectionRevision:
        revision = ConnectionRevision(
            connection_id=row.id, version=row.revision, base_url=row.base_url, encrypted_api_key=row.encrypted_api_key
        )
        session.add(revision)
        await session.flush()
        return revision

    async def list_connections(self, session: AsyncSession, principal: Principal) -> list[ConnectionInfo]:
        principal.require_any_permission('ai:models:read', 'ai:models:write')
        rows = await session.scalars(
            select(ModelConnection)
            .where(ModelConnection.deleted_at.is_(None))
            .order_by(ModelConnection.name)
            .limit(100)
        )
        return [self._connection_info(row) for row in rows]

    async def update_connection(
        self, session: AsyncSession, principal: Principal, connection_id: UUID, fields: dict[str, object]
    ) -> ConnectionInfo:
        principal.require_permission('ai:models:write')
        row = await session.scalar(
            select(ModelConnection)
            .where(ModelConnection.id == connection_id, ModelConnection.deleted_at.is_(None))
            .with_for_update()
        )
        if row is None:
            raise AppError('connection_not_found', '模型连接不存在', 404)
        for name in ('name', 'base_url'):
            if name in fields:
                setattr(row, name, fields[name])
        if 'api_key' in fields:
            row.encrypted_api_key = self._cipher.encrypt(str(fields['api_key']))
        if 'enabled' in fields:
            row.enabled = bool(fields['enabled'])
        row.revision += 1
        await self._revision(session, row)
        return self._connection_info(row)

    async def delete_connection(
        self,
        session: AsyncSession,
        principal: Principal,
        connection_id: UUID,
        *,
        reference_check: Callable[[AsyncSession, UUID], Awaitable[bool]],
    ) -> ConnectionInfo:
        principal.require_permission('ai:models:write')
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
        return self._connection_info(row)

    async def create_model(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        name: str,
        connection_id: UUID,
        upstream_model: str,
        max_output_tokens: int,
        model_type: Literal['chat', 'embedding', 'rerank'] = 'chat',
        published: bool = True,
        vision_enabled: bool = False,
    ) -> ModelInfo:
        principal.require_permission('ai:models:write')
        connection = await session.scalar(
            select(ModelConnection)
            .where(ModelConnection.id == connection_id, ModelConnection.deleted_at.is_(None))
            .with_for_update()
        )
        if connection is None:
            raise AppError('connection_not_found', '模型连接不存在', 404)
        if vision_enabled and model_type != 'chat':
            raise AppError('unsupported_capability', '仅聊天模型支持vision能力', 422)
        model = ModelDefinition(
            name=name,
            connection_id=connection_id,
            upstream_model=upstream_model,
            max_output_tokens=max_output_tokens,
            model_type=model_type,
            published=published,
            vision_enabled=vision_enabled,
        )
        session.add(model)
        await session.flush()
        return self._model_info(model)

    async def list_models(
        self, session: AsyncSession, *, principal: Principal | None = None, include_disabled: bool = False
    ) -> list[ModelInfo]:
        if include_disabled:
            if principal is None:
                raise AppError('forbidden', '当前账号没有此操作权限', 403)
            principal.require_any_permission('ai:models:read', 'ai:models:write')
        query = select(ModelDefinition).where(ModelDefinition.deleted_at.is_(None))
        if not include_disabled:
            query = query.join(ModelConnection, ModelConnection.id == ModelDefinition.connection_id).where(
                ModelDefinition.enabled.is_(True),
                ModelDefinition.published.is_(True),
                ModelConnection.enabled.is_(True),
                ModelConnection.deleted_at.is_(None),
            )
        rows = await session.scalars(query.order_by(ModelDefinition.id).limit(1000))
        return [self._model_info(row) for row in rows]

    async def update_model(
        self, session: AsyncSession, principal: Principal, model_id: UUID, fields: dict[str, object]
    ) -> ModelInfo:
        principal.require_permission('ai:models:write')
        row = await session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id, ModelDefinition.deleted_at.is_(None))
            .with_for_update()
        )
        if row is None:
            raise AppError('model_unavailable', '模型不存在', 404)
        if 'connection_id' in fields:
            connection = await session.scalar(
                select(ModelConnection)
                .where(ModelConnection.id == fields['connection_id'], ModelConnection.deleted_at.is_(None))
                .with_for_update()
            )
            if connection is None:
                raise AppError('connection_not_found', '模型连接不存在', 404)
        if fields.get('vision_enabled') and row.model_type != 'chat':
            raise AppError('unsupported_capability', '仅聊天模型支持vision能力', 422)
        for name, value in fields.items():
            setattr(row, name, value)
        return self._model_info(row)

    async def delete_model(
        self,
        session: AsyncSession,
        principal: Principal,
        model_id: UUID,
        *,
        reference_check: Callable[[AsyncSession, UUID], Awaitable[bool]],
    ) -> ModelInfo:
        principal.require_permission('ai:models:write')
        row = await session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id, ModelDefinition.deleted_at.is_(None))
            .with_for_update()
        )
        if row is None:
            raise AppError('model_unavailable', '模型不存在', 404)
        if await reference_check(session, model_id):
            raise AppError('model_referenced', '模型仍被授权引用', 409)
        row.deleted_at, row.enabled, row.published = utcnow(), False, False
        return self._model_info(row)

    async def lock_many(self, session: AsyncSession, model_ids: list[UUID]) -> None:
        """Order dependency locks without granting liveness or invocation authority."""
        models = list(
            await session.scalars(
                select(ModelDefinition)
                .where(ModelDefinition.id.in_(model_ids))
                .order_by(ModelDefinition.id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        )
        await session.execute(
            select(ModelConnection)
            .where(ModelConnection.id.in_({item.connection_id for item in models}))
            .order_by(ModelConnection.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def resolve_many(self, session: AsyncSession, model_ids: list[UUID]) -> dict[UUID, ModelBinding]:
        """Freeze binding metadata while allowing invocation foreign-key checks."""
        models = list(
            await session.scalars(
                select(ModelDefinition)
                .where(ModelDefinition.id.in_(model_ids))
                .order_by(ModelDefinition.id)
                .with_for_update(key_share=True)
            )
        )
        if len(models) != len(set(model_ids)) or any(
            not item.enabled or not item.published or item.deleted_at is not None for item in models
        ):
            raise AppError('model_unavailable', '模型不存在或不可用', 404)
        await session.execute(
            select(ModelConnection)
            .where(ModelConnection.id.in_({item.connection_id for item in models}))
            .order_by(ModelConnection.id)
            .with_for_update()
        )
        return {item.id: await self.resolve(session, item.id) for item in models}

    async def resolve(self, session: AsyncSession, model_id: UUID, *, lock: bool = True) -> ModelBinding:
        model_query = select(ModelDefinition).where(ModelDefinition.id == model_id)
        if lock:
            # NO KEY UPDATE still excludes configuration changes and deletion.
            # FOR UPDATE would block settlement's FK key-share while admission
            # waits for the same usage account that settlement already owns.
            model_query = model_query.with_for_update(key_share=True)
        model = await session.scalar(model_query)
        if model is None or not model.enabled or not model.published or model.deleted_at is not None:
            raise AppError('model_unavailable', '模型不存在或不可用', 404)
        connection_query = select(ModelConnection).where(ModelConnection.id == model.connection_id)
        if lock:
            connection_query = connection_query.with_for_update()
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
        return ModelBinding(
            vision_enabled=model.vision_enabled,
            model_id=model.id,
            connection_id=model.connection_id,
            connection_revision_id=revision.id,
            upstream_model=model.upstream_model,
            max_output_tokens=model.max_output_tokens,
            model_type=cast("Literal['chat', 'embedding', 'rerank']", model.model_type),
        )

    async def prepare_provider(self, session: AsyncSession, binding: ModelBinding) -> ModelProvider:
        connection: ModelConnection | ConnectionRevision | None
        if binding.connection_revision_id is None:
            connection = await session.get(ModelConnection, binding.connection_id)
        else:
            connection = await session.get(ConnectionRevision, binding.connection_revision_id)
        if connection is None:
            raise AppError('connection_unavailable', '模型连接不可用', 503)
        {
            'base_url': connection.base_url,
            'api_key': self._cipher.decrypt(connection.encrypted_api_key),
            'model': binding.upstream_model,
            'timeout_seconds': self._timeout,
        }
        if binding.model_type != 'chat':
            return StructuredProvider(
                base_url=connection.base_url,
                api_key=self._cipher.decrypt(connection.encrypted_api_key),
                model=binding.upstream_model,
                timeout_seconds=self._timeout,
                model_type=binding.model_type,
                options=binding.options.model_dump(mode='json', exclude_none=True),
            )
        return OpenAIChatProvider(
            base_url=connection.base_url,
            api_key=self._cipher.decrypt(connection.encrypted_api_key),
            model=binding.upstream_model,
            timeout_seconds=self._timeout,
            max_output_tokens=binding.max_output_tokens,
            options=binding.options.model_dump(mode='json', exclude_none=True),
        )

    async def retained_binding(self, session: AsyncSession, model_id: UUID) -> ModelBinding:
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
        return ModelBinding(
            model_id=model.id,
            connection_id=connection.id,
            connection_revision_id=revision.id,
            upstream_model=model.upstream_model,
            max_output_tokens=model.max_output_tokens,
            model_type=cast("Literal['chat', 'embedding', 'rerank']", model.model_type),
            vision_enabled=model.vision_enabled,
        )

    async def add_rate(self, session: AsyncSession, principal: Principal, model_id: UUID, body: RateCreate) -> RateInfo:
        principal.require_permission('ai:rates:write')
        model = await session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id, ModelDefinition.deleted_at.is_(None))
            .with_for_update()
        )
        if model is None:
            raise AppError('model_unavailable', '模型不存在', 404)
        if body.billing_mode != ('search_unit' if model.model_type == 'rerank' else 'token'):
            raise AppError('invalid_rate', '费率计量方式与模型类型不匹配', 422)
        version = await session.scalar(select(func.max(RateCard.version)).where(RateCard.model_id == model_id))
        row = RateCard(model_id=model_id, version=(version or 0) + 1, **body.model_dump())
        session.add(row)
        await session.flush()
        return RateInfo.model_validate(row, from_attributes=True)

    async def rates(self, session: AsyncSession, principal: Principal, model_id: UUID) -> list[RateInfo]:
        principal.require_any_permission('ai:rates:read', 'ai:rates:write')
        rows = await session.scalars(
            select(RateCard).where(RateCard.model_id == model_id).order_by(RateCard.version.desc())
        )
        return [RateInfo.model_validate(row, from_attributes=True) for row in rows]

    async def latest_rate(self, session: AsyncSession, model_id: UUID) -> RateInfo:
        row = await session.scalar(
            select(RateCard).where(RateCard.model_id == model_id).order_by(RateCard.version.desc()).limit(1)
        )
        if row is None:
            raise AppError('model_not_found', 'Model is unavailable', 404)
        return RateInfo.model_validate(row, from_attributes=True)

    async def billing_metadata(self, session: AsyncSession, model_id: UUID) -> ModelInfo:
        """Model existence and type for billing composition; includes disabled models."""
        row = await session.scalar(
            select(ModelDefinition).where(ModelDefinition.id == model_id, ModelDefinition.deleted_at.is_(None))
        )
        if row is None:
            raise AppError('model_not_found', '模型不存在', 404)
        return self._model_info(row)

    async def billing_models(self, session: AsyncSession) -> list[ModelInfo]:
        """Complete public model inventory for internal billing preparation checks."""
        rows = await session.scalars(
            select(ModelDefinition)
            .join(ModelConnection, ModelConnection.id == ModelDefinition.connection_id)
            .where(
                ModelDefinition.enabled.is_(True),
                ModelDefinition.published.is_(True),
                ModelDefinition.deleted_at.is_(None),
                ModelConnection.enabled.is_(True),
                ModelConnection.deleted_at.is_(None),
            )
            .order_by(ModelDefinition.id)
        )
        return [self._model_info(row) for row in rows]

    async def is_public(self, session: AsyncSession, model_id: UUID) -> bool:
        return (
            await session.scalar(
                select(ModelDefinition.id)
                .join(ModelConnection, ModelConnection.id == ModelDefinition.connection_id)
                .where(
                    ModelDefinition.id == model_id,
                    ModelDefinition.enabled.is_(True),
                    ModelDefinition.published.is_(True),
                    ModelDefinition.deleted_at.is_(None),
                    ModelConnection.enabled.is_(True),
                    ModelConnection.deleted_at.is_(None),
                )
            )
            is not None
        )

    @staticmethod
    def _connection_info(row: ModelConnection) -> ConnectionInfo:
        return ConnectionInfo(id=row.id, name=row.name, base_url=row.base_url, enabled=row.enabled)

    @staticmethod
    def _model_info(row: ModelDefinition) -> ModelInfo:
        kind = cast("Literal['chat', 'embedding', 'rerank']", row.model_type)
        capabilities: tuple[Literal['chat', 'stream', 'embedding', 'rerank', 'vision'], ...] = (
            ('chat', 'stream') if kind == 'chat' else (kind,)
        )
        if kind == 'chat' and row.vision_enabled:
            capabilities = (*capabilities, 'vision')
        return ModelInfo(
            vision_enabled=row.vision_enabled,
            id=row.id,
            name=row.name,
            connection_id=row.connection_id,
            upstream_model=row.upstream_model,
            max_output_tokens=row.max_output_tokens,
            enabled=row.enabled,
            model_type=kind,
            published=row.published,
            capabilities=capabilities,
        )
