"""Safe model names and current availability for an authorized workbench reader."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.models.orm import ModelConnection, ModelDefinition
from zhiheng.modules.overview.public import WorkbenchResource
from zhiheng.shared.principal import Principal


class WorkbenchModels:
    async def recent(
        self, session: AsyncSession, actor: Principal, references: list[tuple[UUID, datetime]]
    ) -> list[WorkbenchResource]:
        if not actor.has_any_permission('platform:models:read') or not references:
            return []
        times = dict(references)
        rows = await session.execute(
            select(ModelDefinition.id, ModelDefinition.name, ModelDefinition.enabled, ModelConnection.enabled)
            .join(ModelConnection, ModelConnection.id == ModelDefinition.connection_id)
            .where(
                ModelDefinition.id.in_(times),
                ModelDefinition.deleted_at.is_(None),
                ModelConnection.deleted_at.is_(None),
            )
        )
        return [
            WorkbenchResource(
                kind='model',
                id=identifier,
                name=name,
                occurred_at=times[identifier],
                status='available' if enabled and connection_enabled else 'disabled',
            )
            for identifier, name, enabled, connection_enabled in rows
        ]
