"""Every graph vector client owns its actual transport and a drained executor."""

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any
from uuid import uuid4

from pymilvus import MilvusClient  # type: ignore[import-untyped]

from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl, rpc_channel
from zhiheng.shared.errors import AppError

_METHODS = frozenset(
    {
        'get_server_version',
        'has_collection',
        'describe_collection',
        'list_indexes',
        'describe_index',
        'get_load_state',
        'search',
        'query',
        'get',
        'upsert',
        'insert',
        'delete',
        'flush',
        'create_collection',
        'create_index',
        'load_collection',
        'drop_collection',
    }
)


class GraphVectorRPC:
    """Synchronous SDK methods and async callers share one absolute IO budget.

    No timeout of a Python Future proves that its writer stopped. Cancellation
    stops the real gRPC future and then waits for the complete client call. An
    arbitrary extension blocking outside gRPC keeps stopped=False until it exits.
    """

    def __init__(self, *, uri: str, token: str, database: str, control: VectorIOControl) -> None:
        self._uri, self._token, self._database, self._control = uri, token, database, control.fork()
        self._prefix = f'graph-vector-{uuid4().hex}'
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=self._prefix)
        self._pending: set[Future[Any]] = set()
        self._lock = threading.Lock()
        self._client: Any = None
        self._channel: Any = None
        self._closing = False
        self._closed = False

    @property
    def pending_count(self) -> int:
        with self._lock:
            return sum(not future.done() for future in self._pending)

    @property
    def live_threads(self) -> int:
        return sum(thread.is_alive() and thread.name.startswith(self._prefix) for thread in threading.enumerate())

    @property
    def stopped(self) -> bool:
        return self._closed and self.pending_count == 0 and self.live_threads == 0

    def _invoke(self, name: str, args: tuple[object, ...], kwargs: dict[str, object]) -> Any:
        timeout = kwargs.pop('timeout', 8) or 8
        if not isinstance(timeout, (float, int)):
            raise AppError('invalid_graph_timeout', '图谱RPC期限无效', 422)
        self._control.method(float(timeout))
        if self._client is None:
            self._channel = rpc_channel(self._uri, self._token, self._database, self._control)
            try:
                self._client = MilvusClient(
                    uri=self._uri,
                    token=self._token,
                    db_name=self._database,
                    timeout=self._control.remaining(),
                    dedicated=True,
                    channel=self._channel,
                    grpc_options={'grpc.enable_retries': 0},
                )
            except BaseException:
                self._channel.close()
                self._channel = None
                raise
        kwargs.update(timeout=None, retry_times=0, retry_on_rate_limit=False)
        try:
            return getattr(self._client, name)(*args, **kwargs)
        except AppError:
            raise
        except Exception:
            self._control.check()
            raise AppError('graph_index_unavailable', '图谱索引RPC未完成', 503) from None

    def _submit(self, name: str, args: tuple[object, ...], kwargs: dict[str, object]) -> Future[Any]:
        if name not in _METHODS:
            raise AppError('unsupported_graph_rpc', '图谱不支持该索引操作', 503)
        with self._lock:
            if self._closing:
                raise AppError('graph_writer_stopped', '图谱索引连接已停止', 409)
            future = self._pool.submit(self._invoke, name, args, kwargs)
            self._pending.add(future)
        future.add_done_callback(self._retire)
        return future

    def _retire(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending.discard(future)

    def call(self, name: str, *args: object, **kwargs: object) -> Any:
        return self._submit(name, args, kwargs).result()

    async def acall(self, name: str, *args: object, **kwargs: object) -> Any:
        future = asyncio.wrap_future(self._submit(name, args, kwargs))
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            self._control.cancel()
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:  # noqa: PERF203 - retain actual writer ownership
                    continue
                except Exception:
                    break
            if future.done() and not future.cancelled():
                future.exception()
            raise

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name not in _METHODS:
            raise AttributeError(name)
        return lambda *args, **kwargs: self.call(name, *args, **kwargs)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
            pending = tuple(future for future in self._pending if not future.done())
        if pending:
            self._control.cancel()
        try:
            for future in pending:
                try:
                    future.result()
                except BaseException:  # noqa: PERF203 - each future must finish before shutdown
                    # The original caller owns the result; close owns termination.
                    pass
            if self._client is not None:
                self._pool.submit(self._client.close).result()
        finally:
            try:
                if self._channel is not None:
                    self._channel.close()
            finally:
                self._pool.shutdown(wait=True, cancel_futures=True)
                with self._lock:
                    self._pending.clear()
                    self._closed = True

    async def aclose(self) -> None:
        future = asyncio.get_running_loop().run_in_executor(None, self.close)
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:  # noqa: PERF203 - shutdown cannot abandon the executor
                continue
        future.result()
