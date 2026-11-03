"""Persistence owned by the chat domain; orchestration uses its public inventory."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.chat.context_orm import ContextSnapshot, ContextState, SummaryTask
from zhiheng.modules.chat.orm import Conversation


class ContextStore:
    async def lock_conversation(self, session: AsyncSession, conversation_id: UUID, owner_id: UUID) -> bool:
        """Internal serialization, including tombstones; grants no execution authority."""
        return (
            await session.scalar(
                select(Conversation.id)
                .where(Conversation.id == conversation_id, Conversation.owner_id == owner_id)
                # Exclude another admission without blocking completion's FK key-share.
                .with_for_update(key_share=True)
            )
            is not None
        )

    async def find_state(self, session: AsyncSession, conversation_id: UUID, owner_id: UUID) -> ContextState | None:
        result: ContextState | None = await session.scalar(
            select(ContextState).where(
                ContextState.conversation_id == conversation_id,
                ContextState.owner_id == owner_id,
            )
        )
        return result

    async def retry_request(self, session: AsyncSession, owner_id: UUID, request_id: UUID) -> SummaryTask | None:
        result: SummaryTask | None = await session.scalar(
            select(SummaryTask).where(
                SummaryTask.owner_id == owner_id,
                SummaryTask.retry_request_id == request_id,
            )
        )
        return result

    async def state(
        self, session: AsyncSession, conversation_id: UUID, owner_id: UUID, *, lock: bool = False
    ) -> ContextState:
        await session.execute(
            insert(ContextState).values(conversation_id=conversation_id, owner_id=owner_id).on_conflict_do_nothing()
        )
        query = select(ContextState).where(
            ContextState.conversation_id == conversation_id, ContextState.owner_id == owner_id
        )
        if lock:
            query = query.with_for_update()
        state = await session.scalar(query.execution_options(populate_existing=True))
        assert state is not None
        return state

    async def mark_completed(self, session: AsyncSession, conversation_id: UUID, owner_id: UUID, run_id: UUID) -> None:
        await session.execute(
            insert(ContextState)
            .values(
                conversation_id=conversation_id,
                owner_id=owner_id,
                dirty=True,
                last_run_id=run_id,
            )
            .on_conflict_do_update(
                index_elements=[ContextState.conversation_id], set_={'dirty': True, 'last_run_id': run_id}
            )
        )

    async def pending(self, session: AsyncSession) -> list[tuple[UUID, UUID]]:
        return list(
            (
                await session.execute(
                    select(ContextState.conversation_id, ContextState.owner_id)
                    .where(
                        or_(ContextState.dirty.is_(True), ContextState.pending_task_id.is_not(None)),
                    )
                    .order_by(ContextState.conversation_id)
                    .limit(100)
                )
            ).tuples()
        )

    async def task(self, session: AsyncSession, task_id: UUID) -> SummaryTask | None:
        return await session.get(SummaryTask, task_id)

    async def snapshot(self, session: AsyncSession, run_id: UUID) -> ContextSnapshot | None:
        return await session.get(ContextSnapshot, run_id)

    async def latest_snapshot(self, session: AsyncSession, conversation_id: UUID) -> ContextSnapshot | None:
        result: ContextSnapshot | None = await session.scalar(
            select(ContextSnapshot)
            .where(ContextSnapshot.conversation_id == conversation_id)
            .order_by(
                ContextSnapshot.created_at.desc(),
                ContextSnapshot.run_id.desc(),
            )
            .limit(1)
        )
        return result
