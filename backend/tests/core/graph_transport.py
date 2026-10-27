"""Fault transport: a receiver may commit an accepted create after caller cancellation."""

import os
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

import grpc
import pytest


@contextmanager
def delayed_commit_transport() -> Iterator[tuple[str, threading.Event, threading.Event, threading.Event]]:
    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus required')
    upstream = grpc.insecure_channel(urlsplit(uri).netloc, options=[('grpc.enable_retries', 0)])
    entered, release, committed = threading.Event(), threading.Event(), threading.Event()

    class Proxy(grpc.GenericRpcHandler):
        def service(self, details: Any) -> Any:
            def forward(request: bytes, context: Any) -> bytes:
                late = details.method.rsplit('/', 1)[-1] == 'CreateCollection'
                if late:
                    entered.set()
                    if not release.wait(15):
                        context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, 'owned probe release bound')
                response = upstream.unary_unary(details.method)(
                    request, timeout=8 if late else context.time_remaining(), metadata=context.invocation_metadata()
                )
                if late:
                    committed.set()
                return response

            return grpc.unary_unary_rpc_method_handler(forward)

    pool = ThreadPoolExecutor(max_workers=4)
    server = grpc.server(pool)
    server.add_generic_rpc_handlers((Proxy(),))
    port = server.add_insecure_port('127.0.0.1:0')
    server.start()
    try:
        yield f'http://127.0.0.1:{port}', entered, release, committed
    finally:
        release.set()
        server.stop(0).wait(timeout=3)
        pool.shutdown(wait=True)
        upstream.close()
