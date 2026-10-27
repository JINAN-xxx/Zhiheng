"""One operation owns its channel, deadline and every actual unary RPC future."""

import threading
from datetime import datetime
from time import monotonic
from typing import Any

import grpc  # type: ignore[import-untyped]
from pymilvus.client.connection_manager import ConnectionConfig  # type: ignore[import-untyped]
from pymilvus.client.utils import check_status  # type: ignore[import-untyped]
from pymilvus.exceptions import ErrorCode, MilvusException  # type: ignore[import-untyped]
from pymilvus.grpc_gen.common_pb2 import RateLimit, Status  # type: ignore[import-untyped]

from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


class VectorIOControl:
    def __init__(
        self, *, timeout_seconds: float = 60, deadline: datetime | None = None, lease_until: datetime | None = None
    ) -> None:
        now = monotonic()
        self._deadline = now + (max(0, (deadline - utcnow()).total_seconds()) if deadline else timeout_seconds)
        self._method_deadline = self._deadline
        self._lease = now + max(0, (lease_until - utcnow()).total_seconds()) if lease_until else None
        self._lock, self._cancelled = threading.Lock(), threading.Event()

    def renew(self, lease_until: datetime) -> None:
        """Only called after the fenced SQL renewal transaction commits."""
        with self._lock:
            if self._lease is not None and monotonic() >= self._lease:
                return
            self._lease = monotonic() + max(0, (lease_until - utcnow()).total_seconds())

    def cancel(self) -> None:
        self._cancelled.set()

    def method(self, seconds: float) -> None:
        self.check()
        self._method_deadline = min(self._deadline, monotonic() + seconds)

    def check(self) -> None:
        if self._cancelled.is_set():
            raise AppError('index_operation_cancelled', '索引操作已取消', 409)
        with self._lock:
            if self._lease is not None and monotonic() >= self._lease:
                raise AppError('execution_lost', '知识执行权已失效', 409)
        if monotonic() >= min(self._deadline, self._method_deadline):
            raise AppError('knowledge_timeout', '知识索引IO超过执行期限', 503)

    def remaining(self) -> float:
        self.check()
        return max(0.001, min(self._deadline, self._method_deadline) - monotonic())

    def result(self, future: Any) -> Any:
        try:
            while True:
                try:
                    result = future.result(timeout=min(0.1, self.remaining()))
                    break
                except grpc.FutureTimeoutError:
                    continue
            self.check()
            status = result if isinstance(result, Status) else getattr(result, 'status', None)
            if status is not None:
                try:
                    check_status(status)
                except MilvusException as error:
                    # Other protocol statuses retain SDK semantics, including
                    # DescribeCollection(not found) used by has_collection().
                    if (
                        error.code == ErrorCode.RATE_LIMIT
                        or error.compatible_code == RateLimit
                        or 'STREAMING_CODE_REPLICATE_VIOLATION' in str(error.message)
                    ):
                        raise AppError('knowledge_unavailable', '知识索引RPC未完成', 503) from None
            return result
        except (grpc.RpcError, grpc.FutureCancelledError, MilvusException):
            # Normalize at the transport boundary, before SDK retry/recovery decorators.
            raise AppError('knowledge_unavailable', '知识索引RPC未完成', 503) from None
        finally:
            if not future.done():
                future.cancel()
                while not future.done():
                    try:
                        future.result(timeout=0.1)
                    except grpc.FutureTimeoutError:  # noqa: PERF203 - drain the cancelled C-core call
                        continue
                    except (grpc.RpcError, grpc.FutureCancelledError):
                        break


class _UnaryRPC:
    def __init__(self, target: Any, control: VectorIOControl) -> None:
        self._target, self._control = target, control

    def _invoke(
        self,
        request: object,
        timeout: float | None = None,
        metadata: Any = None,
        credentials: Any = None,
        wait_for_ready: bool | None = None,
        compression: Any = None,
    ) -> tuple[Any, Any]:
        remaining = self._control.remaining()
        try:
            future = self._target.future(
                request,
                timeout=min(remaining, timeout) if timeout is not None else remaining,
                metadata=metadata,
                credentials=credentials,
                wait_for_ready=wait_for_ready,
                compression=compression,
            )
        except grpc.RpcError:
            raise AppError('knowledge_unavailable', '知识索引RPC未完成', 503) from None
        return self._control.result(future), future

    def __call__(self, request: object, *args: Any, **kwargs: Any) -> Any:
        result, _ = self._invoke(request, *args, **kwargs)
        return result

    def future(self, request: object, *args: Any, **kwargs: Any) -> Any:
        # The adapter exclusively uses synchronous SDK APIs. Complete/drain here
        # even when the SDK chooses .future(), so all three call styles share policy.
        _, future = self._invoke(request, *args, **kwargs)
        return future

    def with_call(self, request: object, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        return self._invoke(request, *args, **kwargs)


class _UnsupportedRPC:
    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise AppError('unsupported_index_rpc', '知识索引不支持流式RPC', 503)

    future = __call__
    with_call = __call__


class _RPCChannel:
    def __init__(self, target: Any, control: VectorIOControl) -> None:
        self._target, self._control = target, control

    def unary_unary(self, *args: Any, **kwargs: Any) -> _UnaryRPC:
        return _UnaryRPC(self._target.unary_unary(*args, **kwargs), self._control)

    def unary_stream(self, *args: Any, **kwargs: Any) -> Any:
        return _UnsupportedRPC()

    stream_unary = unary_stream
    stream_stream = unary_stream

    def subscribe(self, *args: Any, **kwargs: Any) -> None:
        self._target.subscribe(*args, **kwargs)

    def unsubscribe(self, *args: Any, **kwargs: Any) -> None:
        self._target.unsubscribe(*args, **kwargs)

    def close(self) -> None:
        self._target.close()


def rpc_channel(uri: str, token: str, database: str, control: VectorIOControl) -> _RPCChannel:
    control.check()
    config = ConnectionConfig.from_uri(uri, token=token, db_name=database)
    if config.is_global:
        raise AppError('unsupported_index_endpoint', '知识索引要求直接Milvus服务地址', 503)
    options = [
        ('grpc.enable_retries', 0),
        ('grpc.max_send_message_length', -1),
        ('grpc.max_receive_message_length', -1),
    ]
    secure = config.get_handler_kwargs().get('secure', False)
    channel = (
        grpc.secure_channel(config.address, grpc.ssl_channel_credentials(), options=options)
        if secure
        else grpc.insecure_channel(config.address, options=options)
    )
    return _RPCChannel(channel, control)
