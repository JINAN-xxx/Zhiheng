"""Deleted conversation checkpoints are reclaimed without deleting native facts."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from psycopg.errors import RaiseException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.core.chat_support import admit, completed, conversation
from zhiheng.application.chat import agent as chat_agent_module
from zhiheng.application.chat.agent import ChatAgentService
from zhiheng.bootstrap.migrations import migrate
from zhiheng.infrastructure.agents.adapters.checkpoints import CheckpointStore
from zhiheng.modules.chat.context_memory import ContextMemory
from zhiheng.modules.chat.context_orm import ContextState
from zhiheng.modules.chat.orm import Conversation
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from zhiheng.bootstrap.settings import Settings


async def test_deleted_checkpoint_candidates_are_due_tombstones_and_bounded(settings: Settings) -> None:
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, wrong_owner = uuid4(), uuid4()
    due = [uuid4() for _ in range(26)]
    live, archived, deferred, mismatch = uuid4(), uuid4(), uuid4(), uuid4()
    try:
        async with sessions.begin() as session:
            session.add_all(
                [
                    IdentityUser(id=owner, username='cleanup-owner', password_hash='unused'),
                    IdentityUser(id=wrong_owner, username='other-owner', password_hash='unused'),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    *(Conversation(id=cid, owner_id=owner, deleted_at=utcnow()) for cid in due),
                    Conversation(id=live, owner_id=owner),
                    Conversation(id=archived, owner_id=owner, archived=True),
                    Conversation(id=deferred, owner_id=owner, deleted_at=utcnow()),
                    Conversation(id=mismatch, owner_id=owner, deleted_at=utcnow()),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    *(ContextState(conversation_id=cid, owner_id=owner) for cid in due),
                    ContextState(conversation_id=live, owner_id=owner),
                    ContextState(conversation_id=archived, owner_id=owner),
                    ContextState(
                        conversation_id=deferred, owner_id=owner, checkpoint_cleanup_after=utcnow() + timedelta(hours=1)
                    ),
                    ContextState(conversation_id=mismatch, owner_id=wrong_owner),
                ]
            )
        async with sessions() as session:
            found = await ContextMemory().deleted_checkpoints(session)
            assert len(found) == 25
            assert {cid for cid, _ in found} <= set(due)
            assert all(uid == owner for _, uid in found)
    finally:
        await engine.dispose()


async def test_mark_checkpoint_cleaned_rechecks_owner_tombstone_and_revisits_late_writes(settings: Settings) -> None:
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, deleted, live = uuid4(), uuid4(), uuid4()
    memory = ContextMemory()
    try:
        async with sessions.begin() as session:
            session.add(IdentityUser(id=owner, username='cleanup-owner', password_hash='unused'))
            await session.flush()
            session.add_all(
                [
                    Conversation(id=deleted, owner_id=owner, deleted_at=utcnow()),
                    Conversation(id=live, owner_id=owner),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    ContextState(
                        conversation_id=cid, owner_id=owner, agent_checkpoint_id='head', agent_source_hash='a' * 64
                    )
                    for cid in (deleted, live)
                ]
            )
        async with sessions.begin() as session:
            assert not await memory.mark_checkpoint_cleaned(session, deleted, uuid4())
            assert not await memory.mark_checkpoint_cleaned(session, live, owner)
            assert await memory.mark_checkpoint_cleaned(session, deleted, owner)
        async with sessions.begin() as session:
            state = await session.get(ContextState, deleted)
            assert state is not None and state.agent_checkpoint_id is None and state.agent_source_hash is None
            assert state.checkpoint_cleanup_after is not None
            assert utcnow() + timedelta(minutes=59) <= state.checkpoint_cleanup_after <= utcnow() + timedelta(hours=1)
            assert await memory.deleted_checkpoints(session) == []
            untouched = await session.get(ContextState, live)
            assert untouched is not None and untouched.agent_checkpoint_id == 'head'
            state.checkpoint_cleanup_after = utcnow() - timedelta(seconds=1)
        async with sessions() as session:
            assert await memory.deleted_checkpoints(session) == [(deleted, owner)]
    finally:
        await engine.dispose()


async def test_cleanup_claim_rechecks_due_tombstone_and_preserves_head(settings: Settings) -> None:
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, deleted, live, deferred = uuid4(), uuid4(), uuid4(), uuid4()
    memory = ContextMemory()
    try:
        async with sessions.begin() as session:
            session.add(IdentityUser(id=owner, username='cleanup-owner', password_hash='unused'))
            await session.flush()
            session.add_all(
                [
                    Conversation(id=deleted, owner_id=owner, deleted_at=utcnow()),
                    Conversation(id=live, owner_id=owner),
                    Conversation(id=deferred, owner_id=owner, deleted_at=utcnow()),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    ContextState(
                        conversation_id=cid,
                        owner_id=owner,
                        agent_checkpoint_id='accepted-head',
                        agent_source_hash='a' * 64,
                        checkpoint_cleanup_after=utcnow() + timedelta(hours=1) if cid == deferred else None,
                    )
                    for cid in (deleted, live, deferred)
                ]
            )
        async with sessions.begin() as session:
            assert not await memory.claim_checkpoint_cleanup(session, deleted, uuid4())
            assert not await memory.claim_checkpoint_cleanup(session, live, owner)
            assert not await memory.claim_checkpoint_cleanup(session, deferred, owner)
            assert await memory.claim_checkpoint_cleanup(session, deleted, owner)
        async with sessions.begin() as session:
            assert not await memory.claim_checkpoint_cleanup(session, deleted, owner)
            state = await session.get(ContextState, deleted)
            assert state is not None and state.agent_checkpoint_id == 'accepted-head'
            assert state.agent_source_hash == 'a' * 64 and state.checkpoint_cleanup_after is not None
            assert utcnow() + timedelta(seconds=59) <= state.checkpoint_cleanup_after <= utcnow() + timedelta(minutes=1)
            assert await memory.mark_checkpoint_cleaned(session, deleted, owner)
        async with sessions() as session:
            state = await session.get(ContextState, deleted)
            assert state is not None and state.agent_checkpoint_id is None and state.agent_source_hash is None
            assert state.checkpoint_cleanup_after >= utcnow() + timedelta(minutes=59)
    finally:
        await engine.dispose()


async def test_cleanup_failure_backs_off_preserves_head_and_continues_other_threads(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner, failed, healthy, active, live = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    memory = ContextMemory()
    deleted = (failed, healthy, active)

    async def has_active(session: object, conversation_id: UUID) -> bool:
        return conversation_id == active

    async def cleanup(conversation_id: UUID) -> None:
        # No business locks remain while the official saver runs.
        async with sessions.begin() as session:
            assert await session.scalar(
                select(Conversation.id).where(Conversation.id == conversation_id).with_for_update(nowait=True)
            )
        if conversation_id == failed:
            raise ValueError('private provider input must not appear in maintenance logs')

    calls = AsyncMock(side_effect=cleanup)
    service = ChatAgentService(
        SimpleNamespace(
            database=SimpleNamespace(sessions=sessions),
            memory=memory,
            runs=SimpleNamespace(has_active=has_active),
        ),
        AsyncMock(),
        calls,
        trigger_tokens=100,
        keep_tokens=20,
        timeout_seconds=1,
    )
    try:
        async with sessions.begin() as session:
            session.add(IdentityUser(id=owner, username='cleanup-owner', password_hash='unused'))
            await session.flush()
            session.add_all(
                [
                    *(Conversation(id=cid, owner_id=owner, deleted_at=utcnow()) for cid in deleted),
                    Conversation(id=live, owner_id=owner),
                ]
            )
            await session.flush()
            session.add_all(
                [
                    ContextState(
                        conversation_id=cid,
                        owner_id=owner,
                        agent_checkpoint_id='accepted-head',
                        agent_source_hash='a' * 64,
                    )
                    for cid in (*deleted, live)
                ]
            )
        # Include a stale live candidate to verify the locked tombstone check.
        monkeypatch.setattr(
            memory, 'deleted_checkpoints', AsyncMock(return_value=[(cid, owner) for cid in (*deleted, live)])
        )
        await service.cleanup_checkpoints()
        assert [call.args[0] for call in calls.await_args_list] == [failed, healthy]
        assert 'error_type=ValueError' in caplog.text and 'private provider input' not in caplog.text
        async with sessions() as session:
            failed_state = await session.get(ContextState, failed)
            assert failed_state.agent_checkpoint_id == 'accepted-head' and failed_state.agent_source_hash == 'a' * 64
            assert utcnow() + timedelta(seconds=59) <= failed_state.checkpoint_cleanup_after
            healthy_state = await session.get(ContextState, healthy)
            assert healthy_state.agent_checkpoint_id is None
            assert healthy_state.checkpoint_cleanup_after >= utcnow() + timedelta(minutes=59)
            for cid in (active, live):
                untouched = await session.get(ContextState, cid)
                assert untouched.agent_checkpoint_id == 'accepted-head' and untouched.checkpoint_cleanup_after is None
        await service.cleanup_checkpoints()
        assert calls.await_count == 2
        async with sessions.begin() as session:
            state = await session.get(ContextState, failed)
            state.checkpoint_cleanup_after = utcnow() - timedelta(seconds=1)
        calls.side_effect = None
        await service.cleanup_checkpoints()
        assert calls.await_count == 3 and calls.await_args.args == (failed,)
        async with sessions() as session:
            state = await session.get(ContextState, failed)
            assert state.agent_checkpoint_id is None and state.agent_source_hash is None
    finally:
        await engine.dispose()


async def test_cleanup_sweep_budget_preserves_hung_and_unprocessed_candidates(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await migrate(settings)
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = uuid4()
    first, remaining = sorted((uuid4(), uuid4()))
    memory = ContextMemory()
    cancelled = asyncio.Event()

    async def cleanup(conversation_id: UUID) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    calls = AsyncMock(side_effect=cleanup)
    service = ChatAgentService(
        SimpleNamespace(
            database=SimpleNamespace(sessions=sessions),
            memory=memory,
            runs=SimpleNamespace(has_active=AsyncMock(return_value=False)),
        ),
        AsyncMock(),
        calls,
        trigger_tokens=100,
        keep_tokens=20,
        timeout_seconds=1,
    )
    monkeypatch.setattr(chat_agent_module, 'CHECKPOINT_CLEANUP_BUDGET_SECONDS', 0.2)
    try:
        async with sessions.begin() as session:
            session.add(IdentityUser(id=owner, username='cleanup-budget-owner', password_hash='unused'))
            await session.flush()
            session.add_all([Conversation(id=cid, owner_id=owner, deleted_at=utcnow()) for cid in (first, remaining)])
            await session.flush()
            session.add_all(
                [
                    ContextState(
                        conversation_id=cid,
                        owner_id=owner,
                        agent_checkpoint_id='accepted-head',
                        agent_source_hash='a' * 64,
                    )
                    for cid in (first, remaining)
                ]
            )
        loop = asyncio.get_running_loop()
        started = loop.time()
        await service.cleanup_checkpoints()
        assert loop.time() - started < 1
        calls.assert_awaited_once_with(first)
        assert cancelled.is_set()
        async with sessions() as session:
            failed = await session.get(ContextState, first)
            assert failed.agent_checkpoint_id == 'accepted-head' and failed.agent_source_hash == 'a' * 64
            assert failed.checkpoint_cleanup_after > utcnow()
            untouched = await session.get(ContextState, remaining)
            assert untouched.agent_checkpoint_id == 'accepted-head' and untouched.agent_source_hash == 'a' * 64
            assert untouched.checkpoint_cleanup_after is None
            assert await memory.deleted_checkpoints(session) == [(remaining, owner)]
    finally:
        await engine.dispose()


async def test_official_thread_delete_is_idempotent_and_isolates_other_threads_and_runs(settings: Settings) -> None:
    await migrate(settings)
    store = CheckpointStore(settings.database_url.get_secret_value())
    first, other, native_run = uuid4(), uuid4(), uuid4()
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            owner = uuid4()
            session.add(IdentityUser(id=owner, username='checkpoint-owner', password_hash='unused'))
            await session.flush()
            session.add(Conversation(id=first, owner_id=owner, deleted_at=utcnow()))
            await session.flush()
            session.add(
                Run(
                    id=native_run,
                    owner_id=owner,
                    billing_owner_id=owner,
                    resource_type='chat',
                    resource_id=first,
                    client_request_id=uuid4(),
                    fingerprint='b' * 64,
                    payload={},
                    status='completed',
                    usage_status='known',
                    input_tokens=10,
                    output_tokens=5,
                )
            )
        async with store.saver() as saver:
            configs = []
            for cid in (first, other):
                checkpoint = empty_checkpoint()
                checkpoint['channel_values'] = {'messages': [HumanMessage(content='retained input')]}
                checkpoint['channel_versions'] = {'messages': '1'}
                config = await saver.aput(
                    store.config(cid), checkpoint, {'source': 'input', 'step': 0, 'parents': {}}, {'messages': '1'}
                )
                await saver.aput_writes(config, [('audit', 'pending write')], 'test-task')
                configs.append(config)
            assert await saver.aget_tuple(configs[0]) is not None
            assert await saver.aget_tuple(configs[1]) is not None
        # Force the second SDK table deletion to fail. The official delete
        # strategy must roll back its first deletion as well.
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    'CREATE FUNCTION chat_checkpoints.reject_test_delete() RETURNS trigger LANGUAGE plpgsql AS '
                    "$$ BEGIN RAISE EXCEPTION 'test cleanup interrupted'; END $$"
                )
            )
            await connection.execute(
                text(
                    'CREATE TRIGGER reject_test_delete BEFORE DELETE ON chat_checkpoints.checkpoint_blobs '
                    'FOR EACH STATEMENT EXECUTE FUNCTION chat_checkpoints.reject_test_delete()'
                )
            )
        with pytest.raises(RaiseException, match='test cleanup interrupted'):
            await store.delete_thread(first)
        async with store.saver() as saver:
            assert await saver.aget_tuple(configs[0]) is not None
            assert await saver.aget_tuple(configs[1]) is not None
        async with engine.begin() as connection:
            await connection.execute(text('DROP TRIGGER reject_test_delete ON chat_checkpoints.checkpoint_blobs'))
            await connection.execute(text('DROP FUNCTION chat_checkpoints.reject_test_delete()'))
        await store.delete_thread(first)
        await store.delete_thread(first)
        async with store.saver() as saver:
            assert await saver.aget_tuple(configs[0]) is None
            assert await saver.aget_tuple(configs[1]) is not None
        async with sessions() as session:
            preserved = await session.scalar(select(Run).where(Run.id == native_run))
            assert preserved is not None and preserved.input_tokens == 10 and preserved.usage_status == 'known'
            for table in ('checkpoints', 'checkpoint_blobs', 'checkpoint_writes'):
                assert (
                    await session.scalar(
                        text(f'SELECT count(*) FROM chat_checkpoints.{table} WHERE thread_id=:thread'),
                        {'thread': str(first)},
                    )
                    == 0
                )
    finally:
        await engine.dispose()


async def test_deleted_conversation_cleanup_releases_business_locks_and_preserves_runs(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    cid = await conversation(admin)
    run = await admit(admin, cid, model_id, 'checkpoint cleanup integration')
    await completed(admin, app, run['id'])
    async with container.database.sessions() as session:
        before = await session.scalar(select(func.count()).select_from(Run))
    assert (
        await admin.request('DELETE', f'/api/chat/conversations/{cid}', json={'expected_version': 1})
    ).status_code == 204
    async with container.database.sessions() as session:
        snapshot_rows = list(
            await session.scalars(
                text('SELECT to_jsonb(s) FROM chat_context_snapshots s WHERE conversation_id=:id'), {'id': cid}
            )
        )
        assert snapshot_rows
    real_cleanup = container.chat_agent.checkpoint_cleanup
    called = []

    async def checked_cleanup(conversation_id: UUID) -> None:
        # A separate connection can lock the tombstone NOWAIT: saver I/O is outside the business transaction.
        async with container.database.sessions.begin() as session:
            assert await session.scalar(
                select(Conversation.id).where(Conversation.id == conversation_id).with_for_update(nowait=True)
            )
        called.append(conversation_id)
        await real_cleanup(conversation_id)

    monkeypatch.setattr(container.chat_agent, 'checkpoint_cleanup', checked_cleanup)
    await container.chat_agent.cleanup_checkpoints()
    assert called == [UUID(cid)]
    async with container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Run)) == before
        assert (
            list(
                await session.scalars(
                    text('SELECT to_jsonb(s) FROM chat_context_snapshots s WHERE conversation_id=:id'), {'id': cid}
                )
            )
            == snapshot_rows
        )
        row = await session.get(ContextState, UUID(cid))
        assert row.agent_checkpoint_id is None and row.checkpoint_cleanup_after > utcnow()
        assert (
            await session.scalar(
                text('SELECT count(*) FROM chat_checkpoints.checkpoints WHERE thread_id=:id'), {'id': cid}
            )
            == 0
        )
    await container.chat_agent.cleanup_checkpoints()
    assert called == [UUID(cid)]
    assert len(provider.requests) == 1


if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests.core.provider import ProviderServer
