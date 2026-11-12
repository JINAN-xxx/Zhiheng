from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.runtime.orm import Outbox, Run
from zhiheng.shared.time import utcnow


@dataclass(frozen=True)
class OutboxDelivery:
    id: UUID
    run_id: UUID
    hint: object


class OutboxStore:
    async def pending(self, session: AsyncSession, *, payload_path: tuple[str, ...]) -> list[OutboxDelivery]:
        if not payload_path:
            raise ValueError('A delivery hint path is required')
        rows = await session.execute(
            select(Outbox.id, Outbox.run_id, Run.payload[payload_path])
            .join(Run)
            .where(
                Run.status == 'pending',
                or_(Outbox.sent_at.is_(None), Outbox.sent_at < utcnow() - timedelta(seconds=15)),
            )
            .limit(100)
        )
        return [OutboxDelivery(row[0], row[1], row[2]) for row in rows]

    async def mark_sent(self, session: AsyncSession, outbox_id: UUID) -> None:
        await session.execute(update(Outbox).where(Outbox.id == outbox_id).values(sent_at=utcnow()))
