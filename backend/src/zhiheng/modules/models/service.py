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
from zhiheng.shared.time import utcnow


class ModelService:
    def __init__(self, cipher: CredentialCipher, timeout_seconds: float) -> None:
        self._cipher = cipher
        self._timeout = timeout_seconds

    async def create_connection(
        self, session: AsyncSession, principal: Principal, *, name: str, base_url: str, api_key: str
    ) -> ConnectionInfo:
        principal.require_admin()
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
        principal.require_admin()
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
        principal.require_admin()
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
        principal.require_admin()
        row = await session.scalar(
            select(ModelConnection)
            .where(ModelConnection.id == connection_id, ModelConnection.deleted_at.is_(None))
            .with_for_update()
        )
        if row is None:
            raise AppError('connection_not_found', '模型连接不存在', 404)
        # Revisions and all model references remain permanent. Deletion is a tombstone.
        if (
            await session.scalar(
                select(ModelDefinition.id).where(ModelDefinition.connection_id == connection_id).limit(1)
            )
            is not None
        ):
            raise AppError('connection_referenced', '连接仍被模型引用', 409)
        if await reference_check(session, connection_id):
            raise AppError('connection_referenced', '连接仍被历史运行引用', 409)
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
    ) -> ModelInfo:
        principal.require_admin()
        connection = await session.scalar(
            select(ModelConnection)
            .where(ModelConnection.id == connection_id, ModelConnection.deleted_at.is_(None))
            .with_for_update()
        )
        if connection is None:
            raise AppError('connection_not_found', '模型连接不存在', 404)
        model = ModelDefinition(
            name=name,
            connection_id=connection_id,
            upstream_model=upstream_model,
            max_output_tokens=max_output_tokens,
            model_type=model_type,
            published=published,
        )
        session.add(model)
        await session.flush()
        return self._model_info(model)

    async def list_models(self, session: AsyncSession, *, include_disabled: bool = False) -> list[ModelInfo]:
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
        principal.require_admin()
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
        principal.require_admin()
        row = await session.scalar(
            select(ModelDefinition)
            .where(ModelDefinition.id == model_id, ModelDefinition.deleted_at.is_(None))
            .with_for_update()
        )
        if row is None:
            raise AppError('model_unavailable', '模型不存在', 404)
        if await reference_check(session, model_id):
            raise AppError('model_referenced', '模型仍被授权引用', 409)
        if await session.scalar(select(RateCard.id).where(RateCard.model_id == model_id).limit(1)) is not None:
            raise AppError('model_referenced', '模型仍被费率引用', 409)
        row.deleted_at, row.enabled, row.published = utcnow(), False, False
        return self._model_info(row)

    async def resolve_many(self, session: AsyncSession, model_ids: list[UUID]) -> dict[UUID, ModelBinding]:
        """Batch admission locks every model before any shared connection."""
        models = list(
            await session.scalars(
                select(ModelDefinition)
                .where(ModelDefinition.id.in_(model_ids))
                .order_by(ModelDefinition.id)
                .with_for_update()
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

    async def resolve(self, session: AsyncSession, model_id: UUID) -> ModelBinding:
        model = await session.scalar(select(ModelDefinition).where(ModelDefinition.id == model_id).with_for_update())
        if model is None or not model.enabled or not model.published or model.deleted_at is not None:
            raise AppError('model_unavailable', '模型不存在或不可用', 404)
        connection = await session.scalar(
            select(ModelConnection).where(ModelConnection.id == model.connection_id).with_for_update()
        )
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

    async def add_rate(self, session: AsyncSession, principal: Principal, model_id: UUID, body: RateCreate) -> RateInfo:
        principal.require_admin()
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
        principal.require_admin()
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

    @staticmethod
    def _connection_info(row: ModelConnection) -> ConnectionInfo:
        return ConnectionInfo(id=row.id, name=row.name, base_url=row.base_url, enabled=row.enabled)

    @staticmethod
    def _model_info(row: ModelDefinition) -> ModelInfo:
        kind = cast("Literal['chat', 'embedding', 'rerank']", row.model_type)
        capabilities: tuple[Literal['chat', 'stream', 'embedding', 'rerank'], ...] = (
            ('chat', 'stream') if kind == 'chat' else (kind,)
        )
        return ModelInfo(
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
