import asyncio
import os
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest

from zhiheng.modules.knowledge.adapters import milvus
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


async def test_cancelled_sdk_rpc_drains_before_closing_client(monkeypatch: pytest.MonkeyPatch) -> None:
    started, release, closed = threading.Event(), threading.Event(), threading.Event()

    class Client:
        def get_server_version(self, **kwargs: Any) -> str:
            started.set()
            release.wait(2)
            return '2.6.25'

        def close(self) -> None:
            closed.set()

    monkeypatch.setattr(milvus, 'MilvusClient', lambda **kwargs: Client())
    task = asyncio.create_task(milvus.MilvusStore('http://test').available())
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done() and not closed.is_set()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.parametrize('failure', ['rate_limit', 'unavailable'])
async def test_real_sdk_does_not_retry_a_failed_rpc_inside_its_original_timeout(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    import grpc
    from pymilvus import MilvusClient
    from pymilvus.exceptions import ErrorCode, MilvusException

    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus URI required')
    attempts: list[float] = []

    class Unavailable(grpc.RpcError):
        def code(self) -> grpc.StatusCode:
            return grpc.StatusCode.UNAVAILABLE

        def details(self) -> str:
            return 'local transient test fault'

    def client_factory(**kwargs: Any) -> Any:
        client = MilvusClient(**kwargs)
        handler = client._get_connection()
        original = handler._stub.GetVersion

        def call(*args: Any, **options: Any) -> Any:
            attempts.append(time.monotonic())
            time.sleep(0.12)
            if len(attempts) == 1:
                if failure == 'rate_limit':
                    raise MilvusException(code=ErrorCode.RATE_LIMIT, message='local transient test fault')
                raise Unavailable
            return original(*args, **options)

        monkeypatch.setattr(handler._stub, 'GetVersion', call)
        return client

    monkeypatch.setattr(milvus, 'MilvusClient', client_factory)
    with pytest.raises(AppError):
        await milvus.MilvusStore(uri)._call(
            lambda client: client.get_server_version(timeout=0.2),
            timeout_seconds=0.2,
        )
    assert len(attempts) == 1


@contextmanager
def delayed_milvus_transport(
    delay: float, methods: set[str]
) -> Iterator[tuple[str, list[tuple[str, float]], threading.Event]]:
    from urllib.parse import urlsplit

    import grpc

    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus URI required')
    address = urlsplit(uri).netloc
    upstream = grpc.insecure_channel(address, options=[('grpc.enable_retries', 0)])
    records: list[tuple[str, float]] = []
    entered = threading.Event()

    class Proxy(grpc.GenericRpcHandler):
        def service(self, details: Any) -> Any:
            def forward(request: bytes, context: Any) -> bytes:
                records.append((details.method, context.time_remaining()))
                entered.set()
                if details.method.rsplit('/', 1)[-1] in methods:
                    time.sleep(delay)
                if not context.is_active():
                    context.abort(grpc.StatusCode.CANCELLED, 'test caller cancelled')
                return upstream.unary_unary(details.method)(
                    request,
                    timeout=context.time_remaining(),
                    metadata=context.invocation_metadata(),
                )

            return grpc.unary_unary_rpc_method_handler(forward)

    pool = ThreadPoolExecutor(max_workers=4)
    server = grpc.server(pool)
    server.add_generic_rpc_handlers((Proxy(),))
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    try:
        yield f'http://127.0.0.1:{port}', records, entered
    finally:
        server.stop(0).wait(timeout=3)
        pool.shutdown(wait=True)
        upstream.close()


@pytest.mark.parametrize('style', ['__call__', 'future', 'with_call'])
async def test_actual_rpc_call_styles_obey_absolute_deadline(style: str) -> None:
    from zhiheng.modules.knowledge.adapters.milvus_rpc import rpc_channel
    from zhiheng.modules.knowledge.public import VectorIOControl

    with delayed_milvus_transport(0.3, {'GetVersion'}) as (uri, records, _):
        control = VectorIOControl(timeout_seconds=0.1)
        channel = rpc_channel(uri, '', 'default', control)
        started = time.monotonic()
        try:
            rpc = channel.unary_unary('/milvus.proto.milvus.MilvusService/GetVersion')
            with pytest.raises(AppError):
                await asyncio.to_thread(getattr(rpc, style), b'')
            assert time.monotonic() - started < 0.25
            assert len(records) == 1
        finally:
            channel.close()


async def test_constructor_and_nested_sdk_rpc_share_one_deadline() -> None:
    with delayed_milvus_transport(0.15, {'Connect', 'GetVersion'}) as (uri, records, _):
        started = time.monotonic()
        with pytest.raises(AppError):
            await milvus.MilvusStore(uri)._call(
                lambda client: client.get_server_version(timeout=1), timeout_seconds=0.23
            )
        assert time.monotonic() - started < 0.4
        assert [method.rsplit('/', 1)[-1] for method, _ in records] == ['Connect', 'GetVersion']
        assert records[1][1] < records[0][1] - 0.1


@pytest.mark.parametrize('stop', ['cancel', 'lease'])
async def test_in_flight_rpc_is_cancelled_and_drained_when_control_stops(stop: str) -> None:
    from zhiheng.modules.knowledge.adapters.milvus_rpc import rpc_channel
    from zhiheng.modules.knowledge.public import VectorIOControl

    with delayed_milvus_transport(0.8, {'GetVersion'}) as (uri, records, entered):
        control = VectorIOControl(
            timeout_seconds=5, lease_until=utcnow() + timedelta(seconds=0.15) if stop == 'lease' else None
        )
        channel = rpc_channel(uri, '', 'default', control)
        started = time.monotonic()
        try:
            rpc = channel.unary_unary('/milvus.proto.milvus.MilvusService/GetVersion')
            work = asyncio.create_task(asyncio.to_thread(rpc.future, b''))
            assert await asyncio.to_thread(entered.wait, 1)
            if stop == 'cancel':
                control.cancel()
            with pytest.raises(AppError):
                await asyncio.wait_for(work, 0.5)
            assert time.monotonic() - started < 0.5 and len(records) == 1
        finally:
            channel.close()
