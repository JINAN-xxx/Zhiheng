from datetime import timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.runtime.orm import Outbox, Run
from zhiheng.shared.time import utcnow


class OutboxStore:
    async def pending(self, session: AsyncSession) -> list[tuple[UUID, UUID]]:
        rows = await session.execute(
            select(Outbox.id, Outbox.run_id)
            .join(Run)
            .where(
                Run.status == 'pending',
                or_(Outbox.sent_at.is_(None), Outbox.sent_at < utcnow() - timedelta(seconds=15)),
            )
            .limit(100)
        )
        return [(row[0], row[1]) for row in rows]

    async def mark_sent(self, session: AsyncSession, outbox_id: UUID) -> None:
        await session.execute(update(Outbox).where(Outbox.id == outbox_id).values(sent_at=utcnow()))
