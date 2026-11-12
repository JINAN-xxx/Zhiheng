from datetime import timedelta
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.knowledge.orm import GenerationPin, IndexGeneration, KnowledgeBase
from zhiheng.modules.knowledge.vector import VectorSpec
from zhiheng.shared.time import utcnow


class GenerationStore:
    async def pending(self, session: AsyncSession) -> list[UUID]:
        return list(
            await session.scalars(
                select(IndexGeneration.id)
                .where(
                    IndexGeneration.status.in_(('retired', 'deleting', 'deleted')),
                    or_(IndexGeneration.status != 'deleted', IndexGeneration.dimension.is_not(None)),
                    or_(IndexGeneration.dimension.is_(None), IndexGeneration.gc_after <= utcnow()),
                    ~exists(select(GenerationPin.intent_id).where(GenerationPin.generation_id == IndexGeneration.id)),
                    ~exists(select(KnowledgeBase.id).where(KnowledgeBase.active_revision == IndexGeneration.id)),
                )
                .order_by(IndexGeneration.gc_after.asc().nullsfirst(), IndexGeneration.id)
                .limit(50)
            )
        )

    async def claim(self, session: AsyncSession, generation_id: UUID) -> tuple[VectorSpec, bool] | None:
        generation = await session.scalar(
            select(IndexGeneration).where(IndexGeneration.id == generation_id).with_for_update()
        )
        if generation is None or generation.status not in {'retired', 'deleting', 'deleted'}:
            return None
        if generation.status == 'deleted' and generation.dimension is None:
            return None
        if generation.dimension is not None and (generation.gc_after is None or generation.gc_after > utcnow()):
            return None
        pinned = await session.scalar(
            select(GenerationPin.intent_id).where(GenerationPin.generation_id == generation_id).limit(1)
        )
        active = await session.scalar(
            select(KnowledgeBase.id).where(KnowledgeBase.active_revision == generation_id).limit(1)
        )
        if pinned or active:
            return None
        generation.status = 'deleting'
        return (
            VectorSpec(
                generation.base_id,
                generation.id,
                generation.attempt_id,
                generation.collection_name,
                generation.fingerprint,
                generation.dimension,
                generation.retrieval_version,
            ),
            generation.dimension is not None,
        )

    async def finish(self, session: AsyncSession, generation_id: UUID) -> None:
        generation = await session.scalar(
            select(IndexGeneration).where(IndexGeneration.id == generation_id).with_for_update()
        )
        if generation is not None and generation.status == 'deleting':
            generation.status = 'deleted'
            # Retry only this registered namespace if a remote commit finishes
            # after the client deadline. Every pass still checks active manifests/pins.
            generation.gc_after = utcnow() + timedelta(hours=1) if generation.dimension is not None else None
