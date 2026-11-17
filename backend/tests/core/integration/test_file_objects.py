"""Multiple fresh blobs share one registration outcome, including lost commit acknowledgement."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from sqlalchemy.util.concurrency import await_only

from zhiheng.bootstrap.container import Container
from zhiheng.modules.files.orm import FileRecord
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


async def owner(app: FastAPI) -> Principal:
    container = app.state.container
    async with container.database.sessions() as session:
        users = await container.identity.directory(session, search='admin')
        return (await container.identity.principal_for_user(session, users[0].id)).personal()


async def register_pair(container: Container, principal: Principal, keys: tuple[str, str]) -> None:
    async with container.file_objects.registrations(keys) as session:
        original = await container.files.create(
            session,
            principal,
            filename='original.txt',
            content=b'original',
            storage_key=keys[0],
            purpose='work_artifact',
        )
        await container.files.create(
            session,
            principal,
            filename='preview.txt',
            content=b'preview',
            storage_key=keys[1],
            source_file_id=original.id,
            purpose='work_preview',
        )


async def assert_pair(container: Container, keys: tuple[str, str]) -> None:
    async with container.database.sessions() as session:
        records = list(await session.scalars(select(FileRecord)))
        assert {item.storage_key for item in records} == set(keys)
        original = next(item for item in records if item.storage_key == keys[0])
        preview = next(item for item in records if item.storage_key == keys[1])
        assert preview.source_file_id == original.id
    assert await container.files.storage.read(keys[0]) == b'original'
    assert await container.files.storage.read(keys[1]) == b'preview'
    assert container.database.engine.pool.checkedout() == 0


async def test_multi_object_rollback_discards_every_owned_key_once_and_preserves_existing_file(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container, principal = app.state.container, await owner(app)
    preserved_key = await container.files.storage.put(b'preserved')
    async with container.file_objects.registration(preserved_key) as session:
        await container.files.create(
            session,
            principal,
            filename='preserved.txt',
            content=b'preserved',
            storage_key=preserved_key,
            purpose='work_artifact',
        )
    before = await container.files.storage.entries(limit=10)
    keys = (await container.files.storage.put(b'original'), await container.files.storage.put(b'preview'))
    delete = AsyncMock(wraps=container.files.storage.delete)
    monkeypatch.setattr(container.files.storage, 'delete', delete)
    with pytest.raises(AppError, match='rollback'):
        async with container.file_objects.registrations((*keys, keys[0])) as session:
            original = await container.files.create(
                session,
                principal,
                filename='original.txt',
                content=b'original',
                storage_key=keys[0],
                purpose='work_artifact',
            )
            await container.files.create(
                session,
                principal,
                filename='preview.txt',
                content=b'preview',
                storage_key=keys[1],
                source_file_id=original.id,
                purpose='work_preview',
            )
            raise AppError('registration_failed', 'injected metadata rollback', 503)
    assert [call.args[0] for call in delete.await_args_list] == list(keys)
    assert await container.files.storage.entries(limit=10) == before
    assert await container.files.storage.read(preserved_key) == b'preserved'
    async with container.database.sessions() as session:
        records = list(await session.scalars(select(FileRecord)))
        assert [item.storage_key for item in records] == [preserved_key]
    assert container.database.engine.pool.checkedout() == 0


async def test_multi_object_lost_commit_ack_retains_both_registered_blobs(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container, principal = app.state.container, await owner(app)
    keys = (await container.files.storage.put(b'original'), await container.files.storage.put(b'preview'))
    cleanup = AsyncMock(wraps=container.file_objects.discard_unregistered)
    monkeypatch.setattr(container.file_objects, 'discard_unregistered', cleanup)

    def mark_registration(session: Session, _context: object) -> None:
        if any(isinstance(row, FileRecord) and row.storage_key in keys for row in session.new):
            session.info['pair_ack_lost'] = True

    def lose_ack(session: Session) -> None:
        if session.info.pop('pair_ack_lost', False):
            raise AppError('commit_ack_lost', 'injected pair commit acknowledgement loss', 503)

    event.listen(Session, 'after_flush', mark_registration)
    event.listen(Session, 'after_commit', lose_ack)
    try:
        with pytest.raises(AppError, match='acknowledgement'):
            await register_pair(container, principal, keys)
    finally:
        event.remove(Session, 'after_flush', mark_registration)
        event.remove(Session, 'after_commit', lose_ack)
    cleanup.assert_not_awaited()
    await assert_pair(container, keys)


async def test_multi_object_cancel_during_pending_commit_never_discards_either_blob(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container, principal = app.state.container, await owner(app)
    keys = (await container.files.storage.put(b'original'), await container.files.storage.put(b'preview'))
    cleanup = AsyncMock(wraps=container.file_objects.discard_unregistered)
    monkeypatch.setattr(container.file_objects, 'discard_unregistered', cleanup)
    entered, release, committed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    def mark_registration(session: Session, _context: object) -> None:
        if any(isinstance(row, FileRecord) and row.storage_key in keys for row in session.new):
            session.info['pair_pending_commit'] = True

    def pause_commit(session: Session) -> None:
        if session.info.get('pair_pending_commit'):
            entered.set()
            await_only(release.wait())

    def complete_commit(session: Session) -> None:
        if session.info.pop('pair_pending_commit', False):
            committed.set()

    event.listen(Session, 'after_flush', mark_registration)
    event.listen(Session, 'before_commit', pause_commit)
    event.listen(Session, 'after_commit', complete_commit)
    operation = asyncio.create_task(register_pair(container, principal, keys))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(operation, 3)
        cleanup.assert_not_awaited()
        assert await container.files.storage.read(keys[0]) == b'original'
        assert await container.files.storage.read(keys[1]) == b'preview'
        release.set()
        await asyncio.wait_for(committed.wait(), 3)
        cleanup.assert_not_awaited()
        await assert_pair(container, keys)
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)
        event.remove(Session, 'after_flush', mark_registration)
        event.remove(Session, 'before_commit', pause_commit)
        event.remove(Session, 'after_commit', complete_commit)
