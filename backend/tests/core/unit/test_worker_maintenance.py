import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from tests.core.unit.test_worker_process import process_fixture


@pytest.mark.parametrize('failure', ['database', 'timeout'])
async def test_failed_file_sweep_does_not_stop_consumers_dispatch_or_heartbeat(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    from zhiheng.worker import process

    worker, events, _ = process_fixture(monkeypatch)
    times = iter((0.0, 1e20))
    monkeypatch.setattr(process.WorkerProcess, '_next_file_sweep', staticmethod(lambda: next(times)))
    monkeypatch.setattr(process, 'MAINTENANCE_INTERVAL_SECONDS', 0.01)
    attempts = 0
    healthy = asyncio.Event()

    async def publish(*_args: object) -> None:
        if attempts:
            healthy.set()

    async def fail_files(_container: object) -> None:
        nonlocal attempts
        attempts += 1
        if failure == 'timeout':
            raise TimeoutError('private-maintenance-detail')
        raise DBAPIError('private-statement', {'key': 'private-parameter'}, RuntimeError('private-driver-detail'))

    monkeypatch.setattr(process, 'publish_outbox', publish)
    monkeypatch.setattr(process, 'file_maintenance', fail_files)
    running = asyncio.create_task(worker.async_run())
    try:
        await asyncio.wait_for(healthy.wait(), timeout=4)
        assert not running.done() and attempts == 1
        heartbeat = worker.container.redis.pipeline().__aenter__.return_value.execute
        assert worker._dispatch_passes > 1 and heartbeat.await_count > 0
        assert 'first_started' in events and 'second_started' in events
        assert not any(value.endswith('_close') for value in events)
        assert 'File maintenance failed' in caplog.text
        for value in ['private-maintenance-detail', 'private-statement', 'private-parameter', 'private-driver-detail']:
            assert value not in caplog.text
    finally:
        await worker.close()
        await asyncio.gather(running, return_exceptions=True)


async def test_shutdown_still_cancels_an_active_file_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    from zhiheng.worker import process

    worker, _, _ = process_fixture(monkeypatch)
    monkeypatch.setattr(process.WorkerProcess, '_next_file_sweep', staticmethod(lambda: 0.0))
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def files(_container: object) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(process, 'file_maintenance', files)
    running = asyncio.create_task(worker.async_run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
    finally:
        await worker.close()
        await asyncio.gather(running, return_exceptions=True)
    assert cancelled.is_set()


@pytest.mark.parametrize('routine', ['housekeeping', 'file_maintenance'])
async def test_slow_cleanup_does_not_delay_run_recovery(monkeypatch: pytest.MonkeyPatch, routine: str) -> None:
    from zhiheng.worker import process

    worker, _, _ = process_fixture(monkeypatch)
    monkeypatch.setattr(process, 'MAINTENANCE_INTERVAL_SECONDS', 0.01)
    if routine == 'file_maintenance':
        times = iter((0.0, process.time() + 3600))
        monkeypatch.setattr(process.WorkerProcess, '_next_file_sweep', staticmethod(lambda: next(times)))
    entered, cancelled, recovered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def cleanup(_container: object) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def recover(_container: object) -> None:
        if entered.is_set():
            recovered.set()

    monkeypatch.setattr(process, routine, cleanup)
    monkeypatch.setattr(process, 'recover_work', recover)
    running = asyncio.create_task(worker.async_run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        await asyncio.wait_for(recovered.wait(), timeout=2)
        assert not cancelled.is_set()
    finally:
        await worker.close()
        await asyncio.gather(running, return_exceptions=True)
    assert cancelled.is_set()


@pytest.mark.parametrize('failure', ['timeout', 'database'])
async def test_housekeeping_isolates_failures_within_one_pass(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failure: str
) -> None:
    from zhiheng.worker import maintenance

    monkeypatch.setattr(maintenance, 'HOUSEKEEPING_TIMEOUT_SECONDS', 0.01)
    cancelled = asyncio.Event()

    async def fail() -> None:
        if failure == 'database':
            raise DBAPIError('private-statement', {'key': 'private-parameter'}, RuntimeError('private-driver'))
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    skill_drafts = AsyncMock()
    assistant_checkpoints = AsyncMock()
    container = SimpleNamespace(
        database=SimpleNamespace(sessions=SimpleNamespace(begin=MagicMock())),
        skills=SimpleNamespace(cleanup_expired=skill_drafts),
        chat_agent=SimpleNamespace(cleanup_checkpoints=fail),
        assistant_agent=SimpleNamespace(cleanup_checkpoints=assistant_checkpoints),
    )
    await maintenance.housekeeping(container)
    skill_drafts.assert_awaited_once()
    assistant_checkpoints.assert_awaited_once()
    assert cancelled.is_set() == (failure == 'timeout')
    assert 'Chat checkpoint cleanup failed' in caplog.text
    assert 'private-' not in caplog.text


async def test_burst_drains_while_housekeeping_is_busy_and_skips_file_sweeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zhiheng.worker import process

    worker, _, _ = process_fixture(monkeypatch)
    worker.burst = True
    monkeypatch.setattr(process, 'MAINTENANCE_INTERVAL_SECONDS', 0.01)
    quiescent = AsyncMock(return_value=True)
    file_sweep = AsyncMock()
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def cleanup(_container: object) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(worker, '_quiescent', quiescent)
    monkeypatch.setattr(worker, '_sweep_files', file_sweep)
    monkeypatch.setattr(process, 'housekeeping', cleanup)
    await asyncio.wait_for(worker.async_run(), timeout=2)
    assert entered.is_set() and cancelled.is_set()
    assert quiescent.await_count == process.QUIET_PASSES
    assert worker._recovery_passes >= process.QUIET_PASSES
    file_sweep.assert_not_awaited()
