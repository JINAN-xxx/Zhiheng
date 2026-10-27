"""One process-owned SDK loop with separately tracked attempt task groups."""

import asyncio
import concurrent.futures
import threading
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any, TypeVar

from lightrag.kg.shared_storage import finalize_share_data  # type: ignore[import-untyped]

T = TypeVar('T')

_CURRENT_TASKS: ContextVar[set[asyncio.Task[Any]] | None] = ContextVar('sdk_owned_tasks', default=None)


class OwnedSDKLoop:
    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ready = threading.Event()
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _start(self) -> None:
        with self._lock:
            if self.thread is not None:
                return

            def target() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self.loop = loop

                def factory(loop: asyncio.AbstractEventLoop, coro: Any, **kwargs: Any) -> asyncio.Task[Any]:
                    task = asyncio.Task(coro, loop=loop, **kwargs)
                    owned = _CURRENT_TASKS.get()
                    if owned is not None:
                        owned.add(task)
                    return task

                loop.set_task_factory(factory)
                self.ready.set()
                try:
                    loop.run_forever()
                finally:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    loop.run_until_complete(loop.shutdown_default_executor())
                    finalize_share_data()
                    loop.close()

            self.thread = threading.Thread(target=target, name='knowledge-graph-sdk', daemon=False)
            self.thread.start()
        self.ready.wait()

    async def run(self, program: Callable[[], Awaitable[T]]) -> T:
        self._start()
        assert self.loop is not None
        result: concurrent.futures.Future[T] = concurrent.futures.Future()
        task_holder: list[asyncio.Task[None]] = []
        started = threading.Event()

        async def root() -> None:
            try:
                result.set_result(await program())
            except BaseException as error:
                result.set_exception(error)

        def submit() -> None:
            assert self.loop is not None
            task_holder.append(self.loop.create_task(root()))
            started.set()

        self.loop.call_soon_threadsafe(submit)
        future = asyncio.wrap_future(result)
        cancelled = False
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:  # noqa: PERF203 - await the actual owned task cleanup
                if not cancelled:
                    cancelled = True
                    handshake = asyncio.create_task(asyncio.to_thread(started.wait), name='graph-sdk-started')
                    while not handshake.done():
                        try:
                            await asyncio.shield(handshake)
                        except asyncio.CancelledError:  # noqa: PERF203 - one handshake survives every caller cancel
                            continue
                    handshake.result()
                    self.loop.call_soon_threadsafe(task_holder[0].cancel)
            except BaseException:
                break
        if cancelled:
            future.exception()
            raise asyncio.CancelledError
        return future.result()

    async def close(self) -> None:
        if self.loop is not None and self.thread is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)
            joined = asyncio.get_running_loop().run_in_executor(None, self.thread.join)
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:  # noqa: PERF203 - shutdown waits through repeated cancellation
                    continue
            joined.result()
            self.loop = None
            self.thread = None
            self.ready.clear()
