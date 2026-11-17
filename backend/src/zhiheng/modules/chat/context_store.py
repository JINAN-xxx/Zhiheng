"""Persistence owned by the chat domain; orchestration uses its public inventory."""

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.chat.context_orm import ContextSnapshot, ContextState, SummaryTask
from zhiheng.modules.chat.orm import Conversation
from zhiheng.shared.time import utcnow


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

    async def pending(self, session: AsyncSession) -> list[tuple[UUID, UUID]]:
        return list(
            (
                await session.execute(
                    select(ContextState.conversation_id, ContextState.owner_id)
                    .where(ContextState.pending_task_id.is_not(None))
                    .order_by(ContextState.conversation_id)
                    .limit(100)
                )
            ).tuples()
        )

    async def summary_run_ids(
        self, session: AsyncSession, parent_run_id: UUID, owner_id: UUID, *, limit: int
    ) -> list[UUID]:
        rows = await session.scalars(
            select(SummaryTask.run_id)
            .where(
                SummaryTask.parent_run_id == parent_run_id,
                SummaryTask.owner_id == owner_id,
                SummaryTask.run_id.is_not(None),
            )
            .order_by(SummaryTask.created_at, SummaryTask.id)
            .limit(limit)
        )
        return [value for value in rows if value is not None]

    async def summary_parent_id(self, session: AsyncSession, run_id: UUID, owner_id: UUID) -> UUID | None:
        return await session.scalar(
            select(SummaryTask.parent_run_id).where(SummaryTask.run_id == run_id, SummaryTask.owner_id == owner_id)
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

    async def checkpoint_snapshot(
        self,
        session: AsyncSession,
        conversation_id: UUID,
        checkpoint_id: str,
    ) -> ContextSnapshot | None:
        row: ContextSnapshot | None = await session.scalar(
            select(ContextSnapshot)
            .where(
                ContextSnapshot.conversation_id == conversation_id,
                ContextSnapshot.agent_checkpoint_id == checkpoint_id,
            )
            .order_by(ContextSnapshot.created_at.desc())
            .limit(1)
        )
        return row

    async def deleted_checkpoints(self, session: AsyncSession) -> list[tuple[UUID, UUID]]:
        return list(
            (
                await session.execute(
                    select(ContextState.conversation_id, ContextState.owner_id)
                    .join(Conversation, Conversation.id == ContextState.conversation_id)
                    .where(
                        Conversation.deleted_at.is_not(None),
                        Conversation.owner_id == ContextState.owner_id,
                        or_(
                            ContextState.checkpoint_cleanup_after.is_(None),
                            ContextState.checkpoint_cleanup_after <= utcnow(),
                        ),
                    )
                    .order_by(ContextState.checkpoint_cleanup_after.asc().nullsfirst(), ContextState.conversation_id)
                    .limit(25)
                )
            ).tuples()
        )

    async def deleted_checkpoint_state(
        self, session: AsyncSession, conversation_id: UUID, owner_id: UUID, *, due: bool = False
    ) -> ContextState | None:
        query = (
            select(ContextState)
            .join(Conversation, Conversation.id == ContextState.conversation_id)
            .where(
                ContextState.conversation_id == conversation_id,
                ContextState.owner_id == owner_id,
                Conversation.owner_id == owner_id,
                Conversation.deleted_at.is_not(None),
            )
            .with_for_update(of=[Conversation.id, ContextState.conversation_id])
            .execution_options(populate_existing=True)
        )
        if due:
            query = query.where(
                or_(ContextState.checkpoint_cleanup_after.is_(None), ContextState.checkpoint_cleanup_after <= utcnow())
            )
        rows = await session.scalars(query)
        return rows.one_or_none()
