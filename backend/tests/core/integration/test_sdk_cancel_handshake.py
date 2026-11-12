"""Actual caller handshake and original SDK drain; scheduling barriers only."""

import asyncio
import gc
import os
import threading
from collections.abc import Awaitable, Callable, Coroutine
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

import pytest

from tests.core.graph_fixtures import empty_frozen, evidence, graph_role
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.knowledge.adapters.lightrag.owned_loop import _CURRENT_TASKS, OwnedSDKLoop
from zhiheng.modules.knowledge.adapters.lightrag.postgres import CURRENT_STORAGE, PostgresContext
from zhiheng.modules.knowledge.adapters.lightrag.sdk import GraphSDK
from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC
from zhiheng.modules.knowledge.adapters.lightrag.vector import CURRENT_VECTOR
from zhiheng.modules.knowledge.public import GraphSDKCall, GraphVectorManifest, VectorIOControl
from zhiheng.shared.errors import AppError


def awaiting(task: asyncio.Task[Any]) -> list[dict[str, object]]:
    current, chain, seen = task.get_coro(), [], set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        frame = getattr(current, 'cr_frame', None) or getattr(current, 'gi_frame', None)
        if frame is not None:
            entry = {'function': frame.f_code.co_name, 'file': frame.f_code.co_filename, 'line': frame.f_lineno}
            if frame.f_code.co_name == 'run' and frame.f_code.co_filename.endswith('/owned_loop.py'):
                entry['started'] = frame.f_locals['started'].is_set()
            chain.append(entry)
        current = getattr(current, 'cr_await', None) or getattr(current, 'gi_yieldfrom', None)
    return chain


@pytest.mark.parametrize('stage,cancels', [('ready', 2), ('ready', 5), ('not-ready', 3)])
async def test_direct_sdk_repeated_cancel_retains_handshake_and_original_drain(  # noqa: PLR0915 - retain the original executor, handshake and drain scheduling barriers together
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, cancels: int
) -> None:
    await migrate(settings)
    main = asyncio.get_running_loop()
    frozen = empty_frozen()
    attempt = uuid4()
    manifest = GraphVectorManifest(frozen.base_id, uuid4(), attempt, frozen.fingerprint, 2, 'g_' + attempt.hex)
    occupied = tmp_path / 'occupied'
    occupied.write_text('Actual regular working-root file')
    release_executor, release_create, release_close = threading.Event(), threading.Event(), threading.Event()
    executor_entered, handshake_posted, create_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    close_entered, program_done, root_cancel_posted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    contexts, rpcs, submissions, tokens, roots, root_cancels, stopped, executions = [], [], [], [], [], [], [], []
    driver = None

    class ObservedExecutor(ThreadPoolExecutor):
        def __init__(self) -> None:
            super().__init__(max_workers=1, thread_name_prefix='sdk-handshake-test')
            self.actual_futures = []

        def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
            future = super().submit(fn, *args, **kwargs)
            self.actual_futures.append(future)
            if len(self.actual_futures) == 2:
                main.call_soon_threadsafe(handshake_posted.set)
            return future

    def occupy_executor() -> None:
        main.call_soon_threadsafe(executor_entered.set)
        release_executor.wait()

    original_context, original_close = PostgresContext.__init__, PostgresContext.close
    original_rpc, original_submit = GraphVectorRPC.__init__, GraphVectorRPC._submit

    def context_init(owner: PostgresContext, *args: Any, **kwargs: Any) -> None:
        original_context(owner, *args, **kwargs)
        contexts.append(owner)

    def rpc_init(owner: GraphVectorRPC, *args: Any, **kwargs: Any) -> None:
        original_rpc(owner, *args, **kwargs)
        rpcs.append(owner)

    def rpc_submit(
        owner: GraphVectorRPC, name: str, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> Future[Any]:
        submissions.append(name)
        return original_submit(owner, name, args, kwargs)

    async def hold_real_close(context: PostgresContext) -> None:
        if context.db.pool is not None:
            main.call_soon_threadsafe(close_entered.set)
            await asyncio.to_thread(release_close.wait)
        await original_close(context)

    async def no_model(call: GraphSDKCall) -> NoReturn:
        raise AssertionError('Failed initialization cannot enter a model')

    monkeypatch.setattr(PostgresContext, '__init__', context_init)
    monkeypatch.setattr(PostgresContext, 'close', hold_real_close)
    monkeypatch.setattr(GraphVectorRPC, '__init__', rpc_init)
    monkeypatch.setattr(GraphVectorRPC, '_submit', rpc_submit)
    async with graph_role(settings) as (pg, url, role):
        sdk = GraphSDK(
            database_url=url,
            # The occupied workroot fails before any vector RPC (asserted below).
            # Keep the real PG/cancellation checks independent of a Milvus service.
            milvus_uri=os.getenv('ZHIHENG_TEST_MILVUS_URI', 'http://127.0.0.1:1'),
            milvus_token='',
            milvus_database='default',
            working_root=occupied,
        )
        sdk.owned_loop._start()
        sdk_loop = sdk.owned_loop.loop
        create, schedule, run = sdk_loop.create_task, sdk_loop.call_soon_threadsafe, sdk.owned_loop.run

        def observe_create(coroutine: Coroutine[Any, Any, Any], *args: Any, **kwargs: Any) -> asyncio.Task[Any]:
            is_root = getattr(getattr(coroutine, 'cr_code', None), 'co_name', None) == 'root'
            if is_root and stage == 'not-ready':
                main.call_soon_threadsafe(create_entered.set)
                assert release_create.wait(15), 'Only the fixture controls root submission scheduling'
            task = create(coroutine, *args, **kwargs)
            if is_root:
                roots.append(task)
            return task

        def observe_schedule(callback: Callable[..., object], *args: Any, **kwargs: Any) -> asyncio.Handle:
            if getattr(callback, '__name__', '') == 'cancel' and getattr(callback, '__self__', None) in roots:
                root_cancels.append(True)
                main.call_soon_threadsafe(root_cancel_posted.set)
            return schedule(callback, *args, **kwargs)

        async def observe_run(program: Callable[[], Awaitable[Any]]) -> Any:
            async def actual() -> Any:
                executions.append(True)
                before = (CURRENT_STORAGE.get(None), CURRENT_VECTOR.get(None), _CURRENT_TASKS.get())
                try:
                    return await program()
                finally:
                    after = (CURRENT_STORAGE.get(None), CURRENT_VECTOR.get(None), _CURRENT_TASKS.get())
                    tokens.append([a is b for a, b in zip(before, after, strict=True)])
                    main.call_soon_threadsafe(program_done.set)

            return await run(actual)

        monkeypatch.setattr(sdk_loop, 'create_task', observe_create)
        monkeypatch.setattr(sdk_loop, 'call_soon_threadsafe', observe_schedule)
        monkeypatch.setattr(sdk.owned_loop, 'run', observe_run)
        executor, previous_executor = ObservedExecutor(), getattr(main, '_default_executor', None)
        main.set_default_executor(executor)
        busy = main.run_in_executor(None, occupy_executor)
        try:
            await asyncio.wait_for(executor_entered.wait(), timeout=5)
            driver = asyncio.create_task(
                sdk.execute(
                    operation='build',
                    frozen=frozen,
                    manifest=manifest,
                    request={},
                    model=no_model,
                    control=VectorIOControl(timeout_seconds=20),
                    stopped=lambda: stopped.append(True),
                )
            )
            await asyncio.wait_for(create_entered.wait() if stage == 'not-ready' else close_entered.wait(), timeout=10)
            driver.cancel()
            await asyncio.wait_for(handshake_posted.wait(), timeout=5)
            chain = awaiting(driver)
            assert any(row.get('started') is (stage == 'ready') for row in chain if 'started' in row)
            for _ in range(cancels - 1):
                driver.cancel()
                await asyncio.sleep(0)
            done, _ = await asyncio.wait({driver}, timeout=0.05)
            premature = driver in done
            release_create.set()
            await asyncio.wait_for(close_entered.wait(), timeout=10)
            before = {
                'driver_done': premature,
                'program_done': program_done.is_set(),
                'stopped': bool(stopped),
                'actual_pg_sessions': [
                    dict(row) for row in await pg.fetch('SELECT pid,state FROM pg_stat_activity WHERE usename=$1', role)
                ],
                'pool_closed': [context.db.pool is None for context in contexts],
                'await_chain': chain,
                'native_executor_jobs': len(executor.actual_futures),
                'handshake_future_cancelled': executor.actual_futures[1].cancelled(),
            }
            evidence(f'direct-sdk-handshake-{stage}-{cancels}-before', before)
            release_executor.set()
            await busy
            if not premature:
                await asyncio.wait_for(root_cancel_posted.wait(), timeout=5)
                # Cancellation after the handshake must also retain the same actual root future.
                driver.cancel()
                done_after, _ = await asyncio.wait({driver}, timeout=0.05)
                assert driver not in done_after and not program_done.is_set() and not stopped
            release_close.set()
            await asyncio.wait_for(program_done.wait(), timeout=10)
            result = await asyncio.gather(driver, return_exceptions=True)
            sessions = [
                dict(row) for row in await pg.fetch('SELECT pid,state FROM pg_stat_activity WHERE usename=$1', role)
            ]
            evidence(
                f'direct-sdk-handshake-{stage}-{cancels}-after',
                {
                    'sessions': sessions,
                    'stopped_callbacks': len(stopped),
                    'program_executions': len(executions),
                    'root_tasks': len(roots),
                    'root_cancel_posts': len(root_cancels),
                    'same_original_task_tokens': tokens,
                    'native_executor_jobs': len(executor.actual_futures),
                    'handshake_future_cancelled': executor.actual_futures[1].cancelled(),
                    'rpc_submissions': submissions,
                    'rpc_closed': [rpc.stopped for rpc in rpcs],
                    'scope': 'Only controlled scheduling released; original program finished before sdk.close; no PID termination',
                },
            )
            assert not sessions and all(context.db.pool is None and not context.leases for context in contexts)
            assert all(all(value) for value in tokens) and len(stopped) == len(executions) == len(roots) == 1
            assert not rpcs and not submissions and isinstance(result[0], asyncio.CancelledError)
            assert not premature, 'Caller abandoned its actual SDK program during started.wait'
            assert len(executor.actual_futures) == 2 and not executor.actual_futures[1].cancelled()
            assert len(root_cancels) == 1
        finally:
            release_executor.set()
            release_create.set()
            release_close.set()
            await busy
            if driver is not None:
                await asyncio.gather(driver, return_exceptions=True)
            if close_entered.is_set():
                await asyncio.wait_for(program_done.wait(), timeout=10)
            await sdk.close()
            # Restore only after all original SDK/handshake work and its loop joined.
            main._default_executor = previous_executor
            executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.parametrize('outcome', ['value', 'exception'])
@pytest.mark.parametrize('cancel_at_result', [False, True])
async def test_owned_root_result_boundary_preserves_result_and_cancel_priority(
    monkeypatch: pytest.MonkeyPatch, outcome: str, cancel_at_result: bool
) -> None:
    loop, owned = asyncio.get_running_loop(), OwnedSDKLoop()
    runs, unconsumed, roots = [], [], []
    driver = None
    original_wrap, handler = asyncio.wrap_future, loop.get_exception_handler()

    def exception_handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if getattr(context.get('exception'), 'code', '') == 'test_root_result':
            unconsumed.append(context.get('message'))
        elif handler is not None:
            handler(_loop, context)

    def wrapped(source: Future[Any], *args: Any, **kwargs: Any) -> asyncio.Future[Any]:
        if cancel_at_result and not roots:
            roots.append(source)
            # wrap_future runs in the caller task; bind its real cancel method
            # before the post-result GC check deliberately deletes driver.
            target = asyncio.current_task()
            assert target is not None
            source.add_done_callback(lambda _, cancel=target.cancel: loop.call_soon_threadsafe(cancel))
        return original_wrap(source, *args, **kwargs)

    async def program() -> int:
        runs.append(True)
        if outcome == 'exception':
            raise AppError('test_root_result', 'Original root exception', 503)
        return 42

    loop.set_exception_handler(exception_handler)
    monkeypatch.setattr(asyncio, 'wrap_future', wrapped)
    try:
        driver = asyncio.create_task(owned.run(program))
        result = (await asyncio.gather(driver, return_exceptions=True))[0]
        assert len(runs) == 1
        if cancel_at_result:
            assert isinstance(result, asyncio.CancelledError)
        elif outcome == 'exception':
            assert isinstance(result, AppError) and result.code == 'test_root_result'
        else:
            assert result == 42
        await owned.close()
        roots.clear()
        del driver, result
        gc.collect()
        await asyncio.sleep(0)
        assert not unconsumed, 'The completed original root exception was not consumed'
    finally:
        await owned.close()
        loop.set_exception_handler(handler)
