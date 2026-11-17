"""Model directory, local configuration writes and metadata queries."""

from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.models.contracts import (
    ModelConfiguration,
    ModelInfo,
)
from zhiheng.modules.models.orm import ModelConnection, ModelDefinition
from zhiheng.modules.models.projections import catalog_info, model_info
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


class ModelCatalog:
    async def create(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        name: str,
        connection_id: UUID,
        upstream_model: str,
        max_output_tokens: int,
        context_window_tokens: int | None = None,
        capacity_note: str = '',
        model_type: Literal['chat', 'embedding', 'rerank'] = 'chat',
        vision_enabled: bool = False,
    ) -> ModelInfo:
        principal.require_permission('platform:models:create')
        config = ModelConfiguration.from_fields(
            {
                'name': name,
                'connection_id': connection_id,
                'upstream_model': upstream_model,
                'max_output_tokens': max_output_tokens,
                'context_window_tokens': context_window_tokens,
                'capacity_note': capacity_note,
                'model_type': model_type,
                'vision_enabled': vision_enabled,
            }
        )
        connection = await session.scalar(
            select(ModelConnection)
            .where(ModelConnection.id == connection_id, ModelConnection.deleted_at.is_(None))
            .with_for_update()
        )
        if connection is None:
            raise AppError('connection_not_found', '模型连接不存在', 404)
        model = ModelDefinition(**config.model_dump())
        session.add(model)
        await session.flush()
        return catalog_info(model, connection)

    async def update(
        self, session: AsyncSession, principal: Principal, model_id: UUID, fields: dict[str, object]
    ) -> ModelInfo:
        if set(fields) - {'enabled'}:
            principal.require_permission('platform:models:update')
        if 'enabled' in fields:
            principal.require_permission('platform:models:toggle')
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
        allowed = set(ModelConfiguration.model_fields) - {'model_type'} | {'enabled'}
        if set(fields) - allowed:
            raise AppError('invalid_model_patch', '包含不支持的模型配置字段', 422)
        if 'enabled' in fields and type(fields['enabled']) is not bool:
            raise AppError('invalid_model_patch', '模型启停状态须为布尔值', 422)
        values = {name: getattr(row, name) for name in ModelConfiguration.model_fields}
        values.update({name: value for name, value in fields.items() if name != 'enabled'})
        config = ModelConfiguration.from_fields(values)
        for name, value in config.model_dump().items():
            setattr(row, name, value)
        if 'enabled' in fields:
            row.enabled = fields['enabled'] is True
        connection = await session.get(ModelConnection, row.connection_id)
        return catalog_info(row, connection)

    async def remove(
        self,
        session: AsyncSession,
        principal: Principal,
        model_id: UUID,
        *,
        reference_check: Callable[[AsyncSession, UUID], Awaitable[bool]],
    ) -> ModelInfo:
        principal.require_permission('platform:models:delete')
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
        return catalog_info(row, None)

    async def billing_metadata(self, session: AsyncSession, model_id: UUID) -> ModelInfo:
        """Model existence and type for billing composition; includes disabled models."""
        row = await session.scalar(
            select(ModelDefinition).where(ModelDefinition.id == model_id, ModelDefinition.deleted_at.is_(None))
        )
        if row is None:
            raise AppError('model_not_found', '模型不存在', 404)
        return model_info(row)

    async def billing_models(self, session: AsyncSession) -> list[ModelInfo]:
        """Complete enabled model inventory for internal billing preparation checks."""
        rows = await session.scalars(
            select(ModelDefinition)
            .join(ModelConnection, ModelConnection.id == ModelDefinition.connection_id)
            .where(
                ModelDefinition.enabled.is_(True),
                ModelDefinition.deleted_at.is_(None),
                ModelConnection.enabled.is_(True),
                ModelConnection.deleted_at.is_(None),
            )
            .order_by(ModelDefinition.id)
        )
        return [model_info(row) for row in rows]

    async def is_public(self, session: AsyncSession, model_id: UUID) -> bool:
        return (
            await session.scalar(
                select(ModelDefinition.id)
                .join(ModelConnection, ModelConnection.id == ModelDefinition.connection_id)
                .where(
                    ModelDefinition.id == model_id,
                    ModelDefinition.enabled.is_(True),
                    ModelDefinition.deleted_at.is_(None),
                    ModelConnection.enabled.is_(True),
                    ModelConnection.deleted_at.is_(None),
                )
            )
            is not None
        )

    async def list(
        self,
        session: AsyncSession,
        *,
        principal: Principal | None = None,
        include_disabled: bool = False,
        for_test: bool = False,
    ) -> list[ModelInfo]:
        if for_test:
            if principal is None:
                raise AppError('forbidden', '当前账号没有模型试用权限', 403)
            principal.require_permission('platform:models:test')
        if include_disabled:
            if principal is None:
                raise AppError('forbidden', '当前账号没有此操作权限', 403)
            principal.require_any_permission(
                'platform:models:config_read',
                'platform:models:create',
                'platform:models:update',
                'platform:models:delete',
                'platform:models:toggle',
            )
        query = (
            select(ModelDefinition, ModelConnection)
            .outerjoin(ModelConnection, ModelConnection.id == ModelDefinition.connection_id)
            .where(ModelDefinition.deleted_at.is_(None))
        )
        if not include_disabled:
            query = query.where(
                ModelDefinition.enabled.is_(True),
                ModelConnection.enabled.is_(True),
                ModelConnection.deleted_at.is_(None),
            )
        rows = await session.execute(query.order_by(ModelDefinition.id).limit(1000))
        return [catalog_info(model, connection) for model, connection in rows]
