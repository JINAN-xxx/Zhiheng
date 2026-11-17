"""Prepare and commit verified model configurations with original CAS and lock order."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.models.bindings import ModelBindings
from zhiheng.modules.models.catalog import ModelCatalog
from zhiheng.modules.models.contracts import (
    ModelConfiguration,
    ModelInfo,
    ModelSaveCheck,
)
from zhiheng.modules.models.orm import ConnectionRevision, ModelConnection, ModelDefinition
from zhiheng.modules.models.projections import binding_for, model_info
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class ModelConfigurationCommands:
    def __init__(self, catalog: ModelCatalog, bindings: ModelBindings) -> None:
        self._catalog, self._bindings = catalog, bindings

    async def prepare(
        self, session: AsyncSession, principal: Principal, fields: dict[str, object], model_id: UUID | None = None
    ) -> ModelSaveCheck:
        actions = (
            ('create',)
            if model_id is None
            else tuple(
                action
                for action, needed in [('update', bool(set(fields) - {'enabled'})), ('toggle', 'enabled' in fields)]
                if needed
            )
        )
        if not actions:
            actions = ('update',)
        for action in actions:
            principal.require_permission('platform:models:' + action)
        allowed = set(ModelConfiguration.model_fields) | {'enabled'}
        if model_id is not None:
            allowed.discard('model_type')
        if set(fields) - allowed or ('enabled' in fields and type(fields['enabled']) is not bool):
            raise AppError('invalid_model_patch', '包含不支持的模型字段或启停状态', 422)
        previous = None
        values: dict[str, object] = {}
        if model_id is not None:
            row = await session.scalar(
                select(ModelDefinition).where(ModelDefinition.id == model_id, ModelDefinition.deleted_at.is_(None))
            )
            if row is None:
                raise AppError('model_unavailable', '模型不存在', 404)
            previous = model_info(row)
            values = {name: getattr(previous, name) for name in ModelConfiguration.model_fields}
        values.update({key: value for key, value in fields.items() if key in ModelConfiguration.model_fields})
        config = ModelConfiguration.from_fields(values)
        # Catalog validation verifies identity, not capacity declarations or local labels.
        validate_provider = (
            previous is None
            or any(
                getattr(config, field) != getattr(previous, field)
                for field in ('connection_id', 'upstream_model', 'model_type')
            )
            or (fields.get('enabled') is True and previous is not None and not previous.enabled)
        )
        connection = await session.scalar(select(ModelConnection).where(ModelConnection.id == config.connection_id))
        if connection is None or connection.deleted_at is not None or (validate_provider and not connection.enabled):
            raise AppError('connection_unavailable', '请选择已启用且可用的模型连接', 422)
        revision = await session.scalar(
            select(ConnectionRevision).where(
                ConnectionRevision.connection_id == connection.id, ConnectionRevision.version == connection.revision
            )
        )
        if revision is None:
            raise AppError('connection_unavailable', '模型连接版本不可用', 503)
        binding = binding_for(config, model_id=model_id or uuid4(), revision_id=revision.id)
        enabled = fields.get('enabled')
        return ModelSaveCheck(
            config,
            binding,
            previous,
            enabled if isinstance(enabled, bool) else None,
            await self._bindings.provider(session, binding) if validate_provider else None,
            required_actions=actions,
        )

    async def save_verified(self, session: AsyncSession, principal: Principal, check: ModelSaveCheck) -> ModelInfo:
        for action in check.required_actions:
            principal.require_permission('platform:models:' + action)
        if check.previous is not None:
            row = await session.scalar(
                select(ModelDefinition)
                .where(ModelDefinition.id == check.previous.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None or row.deleted_at is not None or model_info(row) != check.previous:
                raise AppError('model_configuration_changed', '模型在校验期间已变化，请刷新后重试', 409)
        connection = await session.scalar(
            select(ModelConnection)
            .where(ModelConnection.id == check.binding.connection_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            connection is None
            or connection.deleted_at is not None
            or (check.provider is not None and not connection.enabled)
        ):
            raise AppError('connection_unavailable', '模型连接在校验期间已停用或删除', 409)
        revision = await session.scalar(
            select(ConnectionRevision.id).where(
                ConnectionRevision.connection_id == connection.id, ConnectionRevision.version == connection.revision
            )
        )
        if revision != check.binding.connection_revision_id:
            raise AppError('model_configuration_changed', '模型连接在保存期间已变化，请刷新后重试', 409)
        fields = check.configuration.model_dump()
        if check.previous is None:
            return await self._catalog.create(session, principal, **fields)
        fields.pop('model_type')
        if check.enabled is not None:
            fields['enabled'] = check.enabled
        if check.required_actions == ('toggle',):
            fields = {'enabled': check.enabled}
        return await self._catalog.update(session, principal, check.previous.id, fields)
