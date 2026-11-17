"""Summary terminal transitions and accepted-head publication are independent."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from tests.core.chat_spec_support import execution_spec
from zhiheng.modules.chat.context_memory import ContextMemory
from zhiheng.modules.chat.context_values import ContextSnapshotDraft, SummaryTaskDraft
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


def _memory() -> tuple[ContextMemory, SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    state = SimpleNamespace(
        conversation_id=uuid4(),
        owner_id=uuid4(),
        version=2,
        pending_task_id=None,
        error_code=None,
        agent_checkpoint_id='accepted-head',
        agent_source_hash='a' * 64,
    )
    task = SimpleNamespace(
        id=uuid4(),
        conversation_id=state.conversation_id,
        owner_id=state.owner_id,
        expected_version=2,
        parent_run_id=uuid4(),
        parent_attempt_id=uuid4(),
        run_id=uuid4(),
        status='pending',
        summary='',
        error_code=None,
    )
    state.pending_task_id = task.id
    snapshot = SimpleNamespace(
        conversation_id=state.conversation_id,
        owner_id=state.owner_id,
        agent_context=execution_spec(expected_head='accepted-head').model_dump(mode='json'),
        agent_checkpoint_id=None,
    )
    memory = ContextMemory()
    memory._store.task = AsyncMock(return_value=task)
    memory._store.state = AsyncMock(return_value=state)
    memory._store.snapshot = AsyncMock(return_value=snapshot)
    return memory, state, task, snapshot


def test_new_summary_draft_requires_parent_execution_identity() -> None:
    with pytest.raises(ValueError, match='parent_run_id'):
        SummaryTaskDraft(
            id=uuid4(),
            conversation_id=uuid4(),
            owner_id=uuid4(),
            expected_version=0,
            covered_count=0,
            source_hash='a' * 64,
            source_refs=[],
            file_refs=[],
            model_binding={},
        )


async def test_agent_summary_completion_is_idempotent_and_cannot_replace_terminal_result() -> None:
    memory, state, task, _ = _memory()
    await memory.complete_agent_summary(None, task.id, ' remembered ')
    assert task.status == 'published' and task.summary == 'remembered'
    assert state.version == 3 and state.pending_task_id is None
    await memory.complete_agent_summary(None, task.id, 'remembered')
    assert state.version == 3
    with pytest.raises(AppError, match='不能替换结果'):
        await memory.complete_agent_summary(None, task.id, 'different')


async def test_stale_summary_version_does_not_clear_pending_or_advance_version() -> None:
    memory, state, task, _ = _memory()
    state.version += 1
    with pytest.raises(AppError, match='版本已失效'):
        await memory.complete_agent_summary(None, task.id, 'summary')
    assert state.pending_task_id == task.id and task.status == 'pending' and state.version == 3


async def test_agent_result_records_orphan_checkpoint_without_publishing_its_head() -> None:
    memory, state, _, snapshot = _memory()
    result = {'checkpoint_id': 'orphan', 'context_reservation_tokens': 100}
    await memory.finish_agent(None, uuid4(), result, accepted=False, source_hash=None)
    assert snapshot.agent_checkpoint_id == 'orphan'
    assert state.agent_checkpoint_id == 'accepted-head' and state.agent_source_hash == 'a' * 64
    await memory.finish_agent(None, uuid4(), result, accepted=True, source_hash='b' * 64)
    assert state.agent_checkpoint_id == 'orphan' and state.agent_source_hash == 'b' * 64
    snapshot.agent_context = execution_spec(expected_head='accepted-head').model_dump(mode='json')
    await memory.finish_agent(None, uuid4(), {'checkpoint_id': 'late-branch'}, accepted=True, source_hash='c' * 64)
    assert state.agent_checkpoint_id == 'orphan' and state.agent_source_hash == 'b' * 64


async def test_frozen_agent_snapshot_keeps_execution_budget_and_source_references() -> None:
    memory = ContextMemory()
    session = SimpleNamespace(add=Mock())
    previous = {'file_id': str(uuid4()), 'sha256': 'a' * 64}
    current = {'file_id': str(uuid4()), 'sha256': 'b' * 64}
    refs = [{'question_id': str(uuid4()), 'reply_id': str(uuid4())}]
    draft = ContextSnapshotDraft(
        conversation_id=uuid4(),
        owner_id=uuid4(),
        source_refs=refs,
        file_refs=[previous],
        agent_context=execution_spec(input_budget=4096),
    )
    run_id = uuid4()
    await memory.save_snapshot(session, run_id, draft, [current])
    stored = session.add.call_args.args[0]
    stored.created_at = utcnow()
    snapshot = memory._snapshot_value(stored)
    assert snapshot is not None and snapshot.agent_context is not None
    assert snapshot.run_id == run_id and snapshot.source_refs == refs
    assert snapshot.file_refs == [previous, current]
    assert snapshot.agent_context.input_budget == 4096
