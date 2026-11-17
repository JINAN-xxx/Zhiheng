from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.knowledge.orm import GenerationPin, IndexGeneration, KnowledgeBase, KnowledgeChunk
from zhiheng.modules.knowledge.vector import VectorSpec
from zhiheng.shared.time import utcnow

CHUNK_CLEANUP_BATCH_SIZE = 1000


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

    async def cleanup_chunks(self, session: AsyncSession, *, limit: int = CHUNK_CLEANUP_BATCH_SIZE) -> int:
        """Reclaim one bounded batch after remote GC, retaining namespace and job facts."""
        if not 1 <= limit <= CHUNK_CLEANUP_BATCH_SIZE:
            raise ValueError('Chunk cleanup limit must be between 1 and 1000')
        generation = await session.scalar(
            select(IndexGeneration)
            .where(
                IndexGeneration.status == 'deleted',
                ~exists(select(GenerationPin.intent_id).where(GenerationPin.generation_id == IndexGeneration.id)),
                ~exists(select(KnowledgeBase.id).where(KnowledgeBase.active_revision == IndexGeneration.id)),
                exists(select(KnowledgeChunk.id).where(KnowledgeChunk.generation_id == IndexGeneration.id)),
            )
            .order_by(IndexGeneration.created_at, IndexGeneration.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if generation is None:
            return 0
        # Pin admission also owns this generation lock. Recheck under that fence;
        # cleanup never acquires base/job/file locks in the reverse direction.
        if generation.status != 'deleted':
            return 0
        if await session.scalar(
            select(GenerationPin.intent_id).where(GenerationPin.generation_id == generation.id).limit(1)
        ) or await session.scalar(
            select(KnowledgeBase.id).where(KnowledgeBase.active_revision == generation.id).limit(1)
        ):
            return 0
        # Materialize the locked IDs: a locking LIMIT subquery embedded in
        # DELETE can be re-evaluated by PostgreSQL and exceed the batch bound.
        chunk_ids = list(
            await session.scalars(
                select(KnowledgeChunk.id)
                .where(KnowledgeChunk.generation_id == generation.id)
                .order_by(KnowledgeChunk.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        if not chunk_ids:
            return 0
        removed = await session.scalars(
            delete(KnowledgeChunk)
            .where(KnowledgeChunk.generation_id == generation.id, KnowledgeChunk.id.in_(chunk_ids))
            .returning(KnowledgeChunk.id)
        )
        return len(removed.all())
