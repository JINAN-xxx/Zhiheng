import asyncio

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
    healthy_passes, attempts = 0, 0
    healthy = asyncio.Event()

    async def maintain(_container: object) -> None:
        nonlocal healthy_passes
        healthy_passes += 1
        if healthy_passes == 3:
            healthy.set()

    async def fail_files(_container: object) -> None:
        nonlocal attempts
        attempts += 1
        if failure == 'timeout':
            raise TimeoutError('private-maintenance-detail')
        raise DBAPIError('private-statement', {'key': 'private-parameter'}, RuntimeError('private-driver-detail'))

    monkeypatch.setattr(process, 'maintain', maintain)
    monkeypatch.setattr(process, 'file_maintenance', fail_files)
    running = asyncio.create_task(worker.async_run())
    try:
        await asyncio.wait_for(healthy.wait(), timeout=4)
        assert not running.done() and attempts == 1
        assert worker._dispatch_passes > 1 and worker.container.redis.set.await_count > 1
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
