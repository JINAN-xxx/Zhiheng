"""Pure model, binding and connection projections; no credential reads or SQL."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from zhiheng.modules.models.contracts import (
    ConnectionInfo,
    ModelAvailability,
    ModelBinding,
    ModelConfiguration,
    ModelInfo,
)

if TYPE_CHECKING:
    from uuid import UUID

    from zhiheng.modules.models.orm import ModelConnection, ModelDefinition


def connection_info(row: ModelConnection) -> ConnectionInfo:
    return ConnectionInfo(id=row.id, name=row.name, base_url=row.base_url, enabled=row.enabled)


def model_info(row: ModelDefinition) -> ModelInfo:
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
        context_window_tokens=row.context_window_tokens,
        enabled=row.enabled,
        capacity_note=row.capacity_note or '',
        model_type=kind,
        capabilities=capabilities,
    )


def binding_for(model: ModelInfo | ModelConfiguration, *, model_id: UUID, revision_id: UUID) -> ModelBinding:
    return ModelBinding(
        model_id=model_id,
        connection_id=model.connection_id,
        connection_revision_id=revision_id,
        upstream_model=model.upstream_model,
        max_output_tokens=model.max_output_tokens,
        context_window_tokens=model.context_window_tokens,
        model_type=model.model_type,
        vision_enabled=model.vision_enabled,
    )


def model_availability(model: ModelDefinition, connection: ModelConnection | None) -> ModelAvailability:
    reason = (
        'model_deleted'
        if model.deleted_at is not None
        else 'model_disabled'
        if not model.enabled
        else 'connection_missing'
        if connection is None
        else 'connection_deleted'
        if connection.deleted_at is not None
        else 'connection_disabled'
        if not connection.enabled
        else None
    )
    return ModelAvailability(available=reason is None, reason=reason)


def catalog_info(model: ModelDefinition, connection: ModelConnection | None) -> ModelInfo:
    return model_info(model).model_copy(update={'availability': model_availability(model, connection)})
