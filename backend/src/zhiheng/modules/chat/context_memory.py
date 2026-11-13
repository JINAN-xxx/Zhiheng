"""Chat owns memory persistence and versioned summary state transitions."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.chat.context_orm import ContextSnapshot, ContextState, SummaryTask
from zhiheng.modules.chat.context_store import ContextStore
from zhiheng.modules.chat.context_values import (
    ContextSnapshotDraft,
    ContextSnapshotValue,
    ContextStateValue,
    SummaryTaskDraft,
    SummaryTaskValue,
)
from zhiheng.shared.errors import AppError


class ContextMemory:
    def __init__(self) -> None:
        self._store = ContextStore()

    async def lock_conversation(self, session: AsyncSession, conversation_id: UUID, owner_id: UUID) -> bool:
        return await self._store.lock_conversation(session, conversation_id, owner_id)

    async def find_state(
        self, session: AsyncSession, conversation_id: UUID, owner_id: UUID
    ) -> ContextStateValue | None:
        row = await self._store.find_state(session, conversation_id, owner_id)
        return ContextStateValue.model_validate(row) if row is not None else None

    async def state(
        self, session: AsyncSession, conversation_id: UUID, owner_id: UUID, *, lock: bool = False
    ) -> ContextStateValue:
        return ContextStateValue.model_validate(await self._store.state(session, conversation_id, owner_id, lock=lock))

    async def pending(self, session: AsyncSession) -> list[tuple[UUID, UUID]]:
        return await self._store.pending(session)

    async def task(self, session: AsyncSession, task_id: UUID) -> SummaryTaskValue | None:
        row = await self._store.task(session, task_id)
        return SummaryTaskValue.model_validate(row) if row is not None else None

    async def retry_request(self, session: AsyncSession, owner_id: UUID, request_id: UUID) -> SummaryTaskValue | None:
        row = await self._store.retry_request(session, owner_id, request_id)
        return SummaryTaskValue.model_validate(row) if row is not None else None

    async def snapshot(self, session: AsyncSession, run_id: UUID) -> ContextSnapshotValue | None:
        row = await self._store.snapshot(session, run_id)
        return ContextSnapshotValue.model_validate(row) if row is not None else None

    async def latest_snapshot(self, session: AsyncSession, conversation_id: UUID) -> ContextSnapshotValue | None:
        row = await self._store.latest_snapshot(session, conversation_id)
        return ContextSnapshotValue.model_validate(row) if row is not None else None

    async def save_snapshot(
        self,
        session: AsyncSession,
        run_id: UUID,
        draft: ContextSnapshotDraft,
        current_refs: list[dict[str, object]],
    ) -> None:
        session.add(
            ContextSnapshot(
                **draft.model_dump(exclude={'file_refs'}),
                run_id=run_id,
                file_refs=[*draft.file_refs, *current_refs],
            )
        )

    async def _state(self, session: AsyncSession, value: ContextStateValue) -> ContextState:
        row = await self._store.find_state(session, value.conversation_id, value.owner_id)
        if row is None or row.version != value.version:
            raise AppError('chat_summary_stale', '会话摘要状态版本已失效', 409)
        return row

    async def mark_dirty(
        self, session: AsyncSession, state: ContextStateValue, *, dirty: bool = True
    ) -> ContextStateValue:
        row = await self._state(session, state)
        row.dirty = dirty
        return ContextStateValue.model_validate(row)

    async def degrade(self, session: AsyncSession, state: ContextStateValue, code: str | None) -> ContextStateValue:
        row = await self._state(session, state)
        row.dirty, row.error_code = False, code
        return ContextStateValue.model_validate(row)

    async def scanned(
        self, session: AsyncSession, state: ContextStateValue, *, covered: int, target: int | None
    ) -> ContextStateValue:
        row = await self._state(session, state)
        row.dirty = False
        if target is not None:
            row.target_covered_count = max(row.target_covered_count, target)
        elif row.target_covered_count <= covered:
            row.target_covered_count = 0
        return ContextStateValue.model_validate(row)

    async def create_task(self, session: AsyncSession, draft: SummaryTaskDraft) -> SummaryTaskValue:
        row = SummaryTask(**draft.model_dump(), status='pending')
        session.add(row)
        await session.flush()
        return SummaryTaskValue.model_validate(row)

    async def attach_run(
        self, session: AsyncSession, state: ContextStateValue, task: SummaryTaskValue, run_id: UUID
    ) -> ContextStateValue:
        row = await self._state(session, state)
        current = await self._store.task(session, task.id)
        if (
            current is None
            or current.expected_version != row.version
            or current.status != 'pending'
            or current.owner_id != row.owner_id
            or row.pending_task_id not in {None, current.id}
        ):
            raise AppError('chat_summary_stale', '会话摘要任务版本已失效', 409)
        current.run_id = run_id
        row.pending_task_id, row.error_code = current.id, None
        return ContextStateValue.model_validate(row)

    async def fail_task(
        self, session: AsyncSession, state: ContextStateValue, task: SummaryTaskValue, code: str
    ) -> ContextStateValue:
        row = await self._state(session, state)
        current = await self._store.task(session, task.id)
        if current is None or current.owner_id != row.owner_id or current.conversation_id != row.conversation_id:
            raise AppError('chat_summary_stale', '会话摘要任务已失效', 409)
        current.status, current.error_code = 'failed', code
        row.pending_task_id, row.error_code, row.dirty = None, code, False
        return ContextStateValue.model_validate(row)

    async def clear_pending(self, session: AsyncSession, state: ContextStateValue) -> ContextStateValue:
        row = await self._state(session, state)
        row.pending_task_id = None
        return ContextStateValue.model_validate(row)

    async def publish(
        self, session: AsyncSession, state: ContextStateValue, task: SummaryTaskValue, summary: str
    ) -> ContextStateValue:
        row = await self._state(session, state)
        current = await self._store.task(session, task.id)
        if (
            current is None
            or current.expected_version != row.version
            or row.pending_task_id != current.id
            or current.status != 'pending'
            or current.run_id is None
            or current.owner_id != row.owner_id
            or current.conversation_id != row.conversation_id
            or not summary.strip()
        ):
            raise AppError('chat_summary_stale', '会话摘要任务版本已失效', 409)
        current.summary, current.status = summary.strip(), 'published'
        row.summary_task_id, row.version = current.id, row.version + 1
        row.pending_task_id, row.error_code, row.dirty = None, None, True
        return ContextStateValue.model_validate(row)
