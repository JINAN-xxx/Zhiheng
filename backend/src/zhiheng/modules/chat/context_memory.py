"""Chat owns accepted checkpoint heads and durable summary child transitions."""

from datetime import timedelta
from uuid import UUID

from pydantic import ValidationError
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
from zhiheng.modules.chat.execution import ChatExecutionSpec
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


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

    async def summary_run_ids(
        self, session: AsyncSession, parent_run_id: UUID, owner_id: UUID, *, limit: int
    ) -> list[UUID]:
        """Billing may follow explicit IDs without reading summaries or conversation content."""
        return await self._store.summary_run_ids(session, parent_run_id, owner_id, limit=limit)

    async def summary_parent_id(self, session: AsyncSession, run_id: UUID, owner_id: UUID) -> UUID | None:
        return await self._store.summary_parent_id(session, run_id, owner_id)

    async def task(self, session: AsyncSession, task_id: UUID) -> SummaryTaskValue | None:
        row = await self._store.task(session, task_id)
        return SummaryTaskValue.model_validate(row) if row is not None else None

    async def snapshot(self, session: AsyncSession, run_id: UUID) -> ContextSnapshotValue | None:
        row = await self._store.snapshot(session, run_id)
        return self._snapshot_value(row)

    async def latest_snapshot(self, session: AsyncSession, conversation_id: UUID) -> ContextSnapshotValue | None:
        row = await self._store.latest_snapshot(session, conversation_id)
        return self._snapshot_value(row)

    @staticmethod
    def _snapshot_value(row: ContextSnapshot | None) -> ContextSnapshotValue | None:
        try:
            return ContextSnapshotValue.model_validate(row) if row is not None else None
        except ValidationError as error:
            raise AppError('chat_context_invalid', '会话执行配置已失效，请新建对话后重试', 409) from error

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
            or current.conversation_id != row.conversation_id
            or current.parent_run_id is None
            or current.parent_attempt_id is None
            or current.run_id not in {None, run_id}
            or row.pending_task_id not in {None, current.id}
        ):
            raise AppError('chat_summary_stale', '会话摘要任务版本已失效', 409)
        current.run_id = run_id
        row.pending_task_id, row.error_code = current.id, None
        return ContextStateValue.model_validate(row)

    async def complete_agent_summary(
        self, session: AsyncSession, task_id: UUID, summary: str | None, error_code: str | None = None
    ) -> None:
        task = await self._store.task(session, task_id)
        if task is None:
            raise AppError('chat_summary_stale', '会话摘要任务已失效', 409)
        normalized = (summary or '').strip()
        status = 'published' if normalized else 'failed'
        failure = None if normalized else error_code or 'chat_summary_incomplete'
        state = await self._store.state(session, task.conversation_id, task.owner_id, lock=True)
        if task.status != 'pending':
            if task.status == status and task.summary == normalized and task.error_code == failure:
                return
            raise AppError('chat_summary_stale', '会话摘要已终结，不能替换结果', 409)
        if (
            state.pending_task_id != task.id
            or state.version != task.expected_version
            or task.parent_run_id is None
            or task.parent_attempt_id is None
            or task.run_id is None
        ):
            raise AppError('chat_summary_stale', '会话摘要任务版本已失效', 409)
        task.summary, task.status, task.error_code = normalized, status, failure
        state.pending_task_id, state.error_code = None, failure
        if normalized:
            state.version += 1

    async def finish_agent(
        self,
        session: AsyncSession,
        run_id: UUID,
        result: dict[str, object] | None,
        *,
        accepted: bool,
        source_hash: str | None,
    ) -> None:
        snapshot = await self._store.snapshot(session, run_id)
        if snapshot is None or snapshot.agent_context is None:
            return
        state = await self._store.state(session, snapshot.conversation_id, snapshot.owner_id, lock=True)
        checkpoint = result.get('checkpoint_id') if result else None
        if isinstance(checkpoint, str) and checkpoint:
            snapshot.agent_checkpoint_id = checkpoint
        metadata = ChatExecutionSpec.model_validate(snapshot.agent_context).completed(result)
        snapshot.agent_context = metadata.model_dump(mode='json')
        if (
            accepted
            and source_hash is not None
            and snapshot.agent_checkpoint_id
            and state.agent_checkpoint_id == metadata.expected_head
        ):
            state.agent_checkpoint_id = snapshot.agent_checkpoint_id
            state.agent_source_hash = source_hash
            state.error_code = None

    async def checkpoint_snapshot(
        self,
        session: AsyncSession,
        conversation_id: UUID,
        checkpoint_id: str,
    ) -> ContextSnapshotValue | None:
        row = await self._store.checkpoint_snapshot(session, conversation_id, checkpoint_id)
        return self._snapshot_value(row)

    async def deleted_checkpoints(self, session: AsyncSession) -> list[tuple[UUID, UUID]]:
        return await self._store.deleted_checkpoints(session)

    async def claim_checkpoint_cleanup(self, session: AsyncSession, conversation_id: UUID, owner_id: UUID) -> bool:
        row = await self._store.deleted_checkpoint_state(session, conversation_id, owner_id, due=True)
        if row is None:
            return False
        # Commit the retry interval before saver I/O. Failure or a lost worker
        # preserves the accepted head while another sweep can retry later.
        row.checkpoint_cleanup_after = utcnow() + timedelta(minutes=1)
        return True

    async def mark_checkpoint_cleaned(self, session: AsyncSession, conversation_id: UUID, owner_id: UUID) -> bool:
        row = await self._store.deleted_checkpoint_state(session, conversation_id, owner_id)
        if row is None:
            return False
        row.agent_checkpoint_id, row.agent_source_hash = None, None
        # A lost worker may finish an old physical saver write after fencing.
        # Revisit only this tombstoned thread; native Run/accounting rows remain.
        row.checkpoint_cleanup_after = utcnow() + timedelta(hours=1)
        return True
