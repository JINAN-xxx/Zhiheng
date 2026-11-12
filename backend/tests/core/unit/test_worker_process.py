import asyncio
import signal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet

from zhiheng.bootstrap.settings import Settings


def settings() -> Settings:
    return Settings(
        database_url='postgresql+asyncpg://user:password@localhost/core',
        redis_url='redis://localhost:6379/0',
        credential_key=Fernet.generate_key().decode(),
    )


def process_fixture(monkeypatch: pytest.MonkeyPatch, failure: str = '') -> tuple[object, list[str], asyncio.Event]:
    from zhiheng.worker import process

    events: list[str] = []
    entered = asyncio.Event()

    class Pool:
        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self, *, close_connection_pool: bool) -> None:
            assert close_connection_pool
            events.append(self.name + '_pool')
            if failure == self.name + '_pool':
                raise RuntimeError(failure)

    class Consumer:
        def __init__(self, name: str) -> None:
            self.name = name
            self._pool = Pool(name)
            self.tasks: dict[str, asyncio.Task[None]] = {}

        async def async_run(self) -> None:
            events.append(self.name + '_started')
            entered.set()
            if self.name == 'first' and failure == 'consumer_run':
                raise RuntimeError(failure)
            if self.name == 'first' and failure == 'consumer_return':
                return
            await asyncio.Event().wait()

        async def close(self) -> None:
            events.append(self.name + '_close')
            if self.name == 'first' and failure == 'consumer_close':
                raise RuntimeError(failure)
            # ARQ close can fail before closing this pool; the owner must do it in finally.

    class Runner:
        poller: asyncio.Task[None] | None = None
        tasks: dict[str, asyncio.Task[None]] = {}

        def __init__(self, *_args: object) -> None:
            pass

        async def start(self) -> None:
            self.poller = asyncio.create_task(asyncio.Event().wait())
            if failure == 'graph_start':
                raise RuntimeError(failure)

        async def close(self) -> None:
            events.append('graph_close')
            if self.poller:
                self.poller.cancel()
                await asyncio.gather(self.poller, return_exceptions=True)
            if failure == 'graph_close':
                raise RuntimeError(failure)

    async def create_pool(*_args: object, **_kwargs: object) -> Pool:
        if failure == 'publisher_start':
            raise RuntimeError(failure)
        return Pool('publisher')

    async def delete(_key: str) -> None:
        events.append('heartbeat_delete')
        if failure == 'heartbeat_delete':
            raise RuntimeError(failure)

    async def publish(*_args: object) -> None:
        if failure == 'dispatch':
            raise RuntimeError(failure)

    container = SimpleNamespace(
        settings=settings(),
        database=SimpleNamespace(require_schema=AsyncMock()),
        redis=SimpleNamespace(set=AsyncMock(), delete=delete),
        graph_jobs=object(),
    )
    monkeypatch.setattr(process, 'create_pool', create_pool)
    monkeypatch.setattr(process, 'GraphRunner', Runner)
    monkeypatch.setattr(process, 'publish_outbox', publish)
    monkeypatch.setattr(process, 'publish_knowledge', AsyncMock())
    monkeypatch.setattr(process, 'maintain', AsyncMock())
    value = process.WorkerProcess(container, (Consumer('first'), Consumer('second')), burst=False)
    return value, events, entered


@pytest.mark.parametrize('failure', ['publisher_start', 'graph_start', 'consumer_run', 'consumer_return', 'dispatch'])
async def test_startup_and_supervised_failures_release_every_resource(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    worker, events, _ = process_fixture(monkeypatch, failure)
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(worker.async_run(), timeout=2)
    await worker.close()
    assert {'first_close', 'first_pool', 'second_close', 'second_pool', 'heartbeat_delete'} <= set(events)
    if failure != 'publisher_start':
        assert {'graph_close', 'publisher_pool'} <= set(events)
    assert before == {sig: signal.getsignal(sig) for sig in before}


@pytest.mark.parametrize(
    'failure', ['consumer_close', 'first_pool', 'graph_close', 'heartbeat_delete', 'publisher_pool']
)
async def test_one_cleanup_failure_does_not_skip_other_owned_resources(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    worker, events, entered = process_fixture(monkeypatch, failure)
    running = asyncio.create_task(worker.async_run())
    await asyncio.wait_for(entered.wait(), timeout=2)
    try:
        with pytest.raises(RuntimeError, match=failure):
            await asyncio.wait_for(worker.close(), timeout=2)
    finally:
        await asyncio.gather(running, return_exceptions=True)
    assert {
        'first_close',
        'first_pool',
        'second_close',
        'second_pool',
        'graph_close',
        'heartbeat_delete',
        'publisher_pool',
    } <= set(events)
    assert events.count('graph_close') == events.count('publisher_pool') == 1


async def test_concurrent_close_and_repeated_cancellation_finish_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    worker, events, entered = process_fixture(monkeypatch)
    running = asyncio.create_task(worker.async_run())
    await asyncio.wait_for(entered.wait(), timeout=2)
    closing, release = asyncio.Event(), asyncio.Event()
    original = worker.consumers[0].close

    async def blocked_close() -> None:
        closing.set()
        await release.wait()
        await original()

    monkeypatch.setattr(worker.consumers[0], 'close', blocked_close)
    first = asyncio.create_task(worker.close())
    await asyncio.wait_for(closing.wait(), timeout=2)
    second = asyncio.create_task(worker.close())
    first.cancel()
    await asyncio.sleep(0)
    first.cancel()
    release.set()
    results = await asyncio.wait_for(asyncio.gather(first, second, running, return_exceptions=True), timeout=2)
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1] is None
    assert events.count('first_close') == events.count('second_close') == events.count('graph_close') == 1
    assert events.count('publisher_pool') == 1


def test_model_lanes_are_bounded_and_do_not_share_worker_context() -> None:
    from zhiheng.worker.main import build_worker

    worker = build_worker(SimpleNamespace(settings=settings()), max_jobs=1)
    assert [consumer.max_jobs for consumer in worker.consumers] == [1, 4, 2, 4]
    assert len({consumer.queue_name for consumer in worker.consumers}) == 4
    assert len({id(consumer.ctx) for consumer in worker.consumers}) == 4
    assert all(not consumer.burst and not consumer._handle_signals for consumer in worker.consumers)


async def test_repeated_cancellation_cannot_abandon_the_outer_container(monkeypatch: pytest.MonkeyPatch) -> None:
    from zhiheng.worker import main

    started, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Resources:
        closed = False
        redis = SimpleNamespace(delete=AsyncMock())

        async def close(self) -> None:
            closing.set()
            await release.wait()
            self.closed = True

    class Worker:
        async def async_run(self) -> None:
            started.set()
            await asyncio.Event().wait()

        async def close(self) -> None:
            pass

    resources = Resources()
    monkeypatch.setattr(main, 'Container', lambda _settings: resources)
    monkeypatch.setattr(main, 'build_worker', lambda _container: Worker())
    running = asyncio.create_task(main.run_worker(settings()))
    await asyncio.wait_for(started.wait(), timeout=2)
    running.cancel()
    await asyncio.wait_for(closing.wait(), timeout=2)
    running.cancel()
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(asyncio.gather(running, return_exceptions=True), timeout=2)
    assert resources.closed


async def test_arq_first_job_failure_waits_for_its_sibling_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    worker, events, _ = process_fixture(monkeypatch)
    consumer = worker.consumers[0]
    close_returned, cleanup_entered, release, finished = (asyncio.Event() for _ in range(4))

    async def first() -> None:
        await asyncio.Event().wait()

    async def sibling() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_entered.set()
            await release.wait()
            finished.set()

    consumer.tasks = {'first': asyncio.create_task(first()), 'sibling': asyncio.create_task(sibling())}
    await asyncio.sleep(0)

    async def arq_close() -> None:
        for task in consumer.tasks.values():
            task.cancel()
        try:
            await asyncio.gather(*consumer.tasks.values())
        finally:
            close_returned.set()

    monkeypatch.setattr(consumer, 'close', arq_close)
    closing = asyncio.create_task(worker.close())
    try:
        await asyncio.wait_for(close_returned.wait(), timeout=2)
        await asyncio.wait_for(cleanup_entered.wait(), timeout=2)
        premature = 'first_pool' in events
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(closing, return_exceptions=True), timeout=2)
    assert finished.is_set() and not premature


@pytest.mark.parametrize('failed', [False, True])
async def test_arq_shielded_finalizer_finishes_before_its_pool_closes(
    monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    from zhiheng.worker.main import build_worker

    worker, _, _ = process_fixture(monkeypatch)
    consumer = build_worker(SimpleNamespace(settings=settings())).consumers[0]
    entered, release, finished, stopped = (asyncio.Event() for _ in range(4))
    closed_after_finish: list[bool] = []

    class Pipeline:
        async def __aenter__(self) -> 'Pipeline':
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        def delete(self, *_args: object) -> None:
            pass

        def zrem(self, *_args: object) -> None:
            pass

        async def execute(self) -> None:
            entered.set()
            await release.wait()
            finished.set()

    class Pool:
        def pipeline(self, *, transaction: bool) -> Pipeline:
            assert transaction
            return Pipeline()

        async def delete(self, _key: str) -> None:
            pass

        async def close(self, *, close_connection_pool: bool) -> None:
            assert close_connection_pool
            closed_after_finish.append(finished.is_set())

    async def job() -> None:
        if failed:
            await asyncio.shield(consumer.finish_failed_job('job', None))
        else:
            await asyncio.shield(consumer.finish_job('job', True, None, 0, False, None, None))

    consumer._pool = Pool()
    task = asyncio.create_task(job())
    loop = asyncio.get_running_loop()
    task.add_done_callback(lambda _task: loop.call_soon(stopped.set))
    consumer.tasks = {'job': task}
    worker.consumers = (consumer,)
    await asyncio.wait_for(entered.wait(), timeout=2)
    closing = asyncio.create_task(worker.close())
    try:
        await asyncio.wait_for(stopped.wait(), timeout=2)
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(closing, return_exceptions=True), timeout=2)
    assert closed_after_finish == [True]


@pytest.mark.parametrize(
    'hint,expected', [('embedding', 'short'), ('rerank', 'short'), ('chat', 'generation'), (None, 'generation')]
)
def test_dispatch_uses_an_opaque_model_type_hint(hint: object, expected: str) -> None:
    from zhiheng.application.execution.dispatch import execution_lane

    assert execution_lane(hint) == expected
