"""Append-only configured currency rates, independent of platform credit prices."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.models.contracts import (
    RateCreate,
    RateInfo,
)
from zhiheng.modules.models.orm import ModelDefinition, RateCard
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class ModelRates:
    async def create(self, session: AsyncSession, principal: Principal, model_id: UUID, body: RateCreate) -> RateInfo:
        principal.require_permission('ai:rates:create')
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

    async def list(self, session: AsyncSession, principal: Principal, model_id: UUID) -> list[RateInfo]:
        principal.require_permission('ai:rates:read')
        rows = await session.scalars(
            select(RateCard).where(RateCard.model_id == model_id).order_by(RateCard.version.desc())
        )
        return [RateInfo.model_validate(row, from_attributes=True) for row in rows]

    async def latest(self, session: AsyncSession, model_id: UUID) -> RateInfo:
        row = await session.scalar(
            select(RateCard).where(RateCard.model_id == model_id).order_by(RateCard.version.desc()).limit(1)
        )
        if row is None:
            raise AppError('model_not_found', 'Model is unavailable', 404)
        return RateInfo.model_validate(row, from_attributes=True)
