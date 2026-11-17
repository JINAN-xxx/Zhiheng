"""Storage scans stay bounded while concurrent registration and purge preserve native state."""

import asyncio
import hashlib
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import event, select

from zhiheng.modules.files.orm import FileReconcileIssue, FileRecord
from zhiheng.shared.storage import StorageInspection, StorageStat

if TYPE_CHECKING:
    from fastapi import FastAPI

    from zhiheng.bootstrap.container import Container
    from zhiheng.shared.principal import Principal


async def _principal(container: 'Container') -> 'Principal':
    async with container.database.sessions() as session:
        users = await container.identity.directory(session, search='admin')
        return await container.identity.principal_for_user(session, users[0].id)


def _record(principal: 'Principal', key: str, index: int, content: bytes = b'bounded') -> FileRecord:
    return FileRecord(
        id=UUID(int=index),
        storage_key=key,
        original_filename=f'{index}.txt',
        display_name=f'{index}.txt',
        size=len(content),
        mime='text/plain',
        sha256=hashlib.sha256(content).hexdigest(),
        owner_id=principal.id,
        uploader_id=principal.id,
        storage_status='normal',
    )


async def test_reconcile_pages_stream_records_and_only_stats_old_orphans(
    app: 'FastAPI', monkeypatch: pytest.MonkeyPatch
) -> None:
    from zhiheng.modules.files import jobs

    container = app.state.container
    principal = await _principal(container)
    monkeypatch.setattr(jobs, 'BATCH_SIZE', 2)
    registered = [await container.files.storage.put(b'bounded') for _ in range(5)]
    orphaned = [await container.files.storage.put(b'orphan') for _ in range(2)]
    recent = await container.files.storage.put(b'recent')
    for key in [*registered, *orphaned]:
        os.utime(container.settings.files_root / key, (1, 1))
    async with container.database.sessions.begin() as session:
        session.add_all(_record(principal, key, index + 1) for index, key in enumerate(registered))
    inspected, stated = [], []
    inspect, stat = container.files.storage.inspect, container.files.storage.stat

    async def record_inspect(key: str) -> StorageInspection:
        inspected.append(key)
        return await inspect(key)

    async def record_stat(key: str) -> StorageStat:
        stated.append(key)
        return await stat(key)

    async def no_content_read(key: str) -> bytes:
        raise AssertionError(f'Maintenance must not materialize object bytes: {key}')

    monkeypatch.setattr(container.files.storage, 'inspect', record_inspect)
    monkeypatch.setattr(container.files.storage, 'stat', record_stat)
    monkeypatch.setattr(container.files.storage, 'read', no_content_read)
    for _ in range(4):
        result = await container.file_jobs.run(principal, 'reconcile')
        assert result.status == 'succeeded' and result.scanned <= 4
    assert set(inspected) == set(registered)
    assert set(stated) == set(orphaned) and recent not in stated
    async with container.database.sessions() as session:
        issues = list(await session.scalars(select(FileReconcileIssue)))
    assert {issue.storage_key for issue in issues} == set(orphaned)
    assert all(issue.kind == 'orphan' and issue.size == len(b'orphan') for issue in issues)


async def test_registered_storage_page_uses_one_registration_query(
    app: 'FastAPI', monkeypatch: pytest.MonkeyPatch
) -> None:
    container = app.state.container
    principal = await _principal(container)
    keys = [await container.files.storage.put(b'bounded') for _ in range(7)]
    for key in keys:
        os.utime(container.settings.files_root / key, (1, 1))
    async with container.database.sessions.begin() as session:
        session.add_all(_record(principal, key, index + 1) for index, key in enumerate(keys))
    queries = []

    def record_query(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if 'WHERE files.storage_key' in statement:
            queries.append(statement)

    async def no_orphan_stat(key: str) -> StorageStat:
        raise AssertionError(f'Registered object must not be checked again as an orphan: {key}')

    monkeypatch.setattr(container.files.storage, 'stat', no_orphan_stat)
    engine = container.database.engine.sync_engine
    event.listen(engine, 'before_cursor_execute', record_query)
    try:
        await container.file_jobs.run(principal, 'reconcile')
    finally:
        event.remove(engine, 'before_cursor_execute', record_query)
    assert len(queries) == 1


@pytest.mark.parametrize('change', ['register', 'refresh'])
async def test_orphan_rechecks_registration_and_age_after_storage_io(
    app: 'FastAPI', monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    container = app.state.container
    principal = await _principal(container)
    key = await container.files.storage.put(b'bounded')
    os.utime(container.settings.files_root / key, (1, 1))
    stat = container.files.storage.stat

    async def concurrently_changed(storage_key: str) -> StorageStat:
        if change == 'register':
            stored = await stat(storage_key)
            async with container.database.sessions.begin() as session:
                session.add(_record(principal, storage_key, 1))
            return stored
        now = datetime.now(timezone.utc).timestamp()
        os.utime(container.settings.files_root / storage_key, (now, now))
        return await stat(storage_key)

    monkeypatch.setattr(container.files.storage, 'stat', concurrently_changed)
    result = await container.file_jobs.run(principal, 'reconcile')
    assert result.status == 'succeeded' and result.changed == 0
    async with container.database.sessions() as session:
        assert await session.scalar(select(FileReconcileIssue.id)) is None


async def test_reconcile_does_not_restore_state_after_concurrent_purge(
    app: 'FastAPI', monkeypatch: pytest.MonkeyPatch
) -> None:
    container = app.state.container
    principal = await _principal(container)
    key = await container.files.storage.put(b'bounded')
    async with container.database.sessions.begin() as session:
        row = _record(principal, key, 1)
        row.status = 'deleted'
        session.add(row)
    started, release = asyncio.Event(), asyncio.Event()
    inspect = container.files.storage.inspect

    async def paused(storage_key: str) -> StorageInspection:
        result = await inspect(storage_key)
        started.set()
        await release.wait()
        return result

    monkeypatch.setattr(container.files.storage, 'inspect', paused)
    running = asyncio.create_task(container.file_jobs.run(principal, 'reconcile'))
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        purged = await container.file_jobs.purge(principal, UUID(int=1), 1)
        assert purged.status == 'purged'
    finally:
        release.set()
        await asyncio.wait_for(running, timeout=5)
    async with container.database.sessions() as session:
        current = await session.get(FileRecord, UUID(int=1))
        assert current is not None and current.status == 'purged' and current.storage_status == 'missing'
        assert await session.scalar(select(FileReconcileIssue.id)) is None


async def test_reconcile_and_repair_detect_same_size_corruption_without_reading_content(
    app: 'FastAPI', monkeypatch: pytest.MonkeyPatch
) -> None:
    from zhiheng.modules.files.contracts import IssueAction
    from zhiheng.shared.errors import AppError

    container = app.state.container
    principal = await _principal(container)
    key = await container.files.storage.put(b'bounded')
    async with container.database.sessions.begin() as session:
        session.add(_record(principal, key, 1))
    path = container.settings.files_root / key
    path.write_bytes(b'changed')

    async def no_content_read(storage_key: str) -> bytes:
        raise AssertionError(f'Integrity checks must not materialize object bytes: {storage_key}')

    monkeypatch.setattr(container.files.storage, 'read', no_content_read)
    result = await container.file_jobs.run(principal, 'reconcile')
    assert result.changed == 1
    async with container.database.sessions() as session:
        issue = await session.scalar(select(FileReconcileIssue))
        current = await session.get(FileRecord, UUID(int=1))
        assert issue is not None and issue.kind == 'size_or_hash'
        assert current is not None and current.storage_status == 'invalid'
    action = IssueAction(expected_version=issue.version, action='repair')
    with pytest.raises(AppError) as error:
        await container.file_jobs.issue_action(principal, issue.id, action)
    assert error.value.code == 'file_integrity_error'
    path.write_bytes(b'bounded')
    repaired = await container.file_jobs.issue_action(principal, issue.id, action)
    assert repaired.status == 'resolved'
    async with container.database.sessions() as session:
        current = await session.get(FileRecord, UUID(int=1))
        assert current is not None and current.storage_status == 'normal'
