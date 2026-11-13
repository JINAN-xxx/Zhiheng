"""One operation owns its channel, deadline and every actual unary RPC future."""

from typing import Any

import grpc  # type: ignore[import-untyped]
from pymilvus.client.connection_manager import ConnectionConfig  # type: ignore[import-untyped]
from pymilvus.client.utils import check_status  # type: ignore[import-untyped]
from pymilvus.exceptions import ErrorCode, MilvusException  # type: ignore[import-untyped]
from pymilvus.grpc_gen.common_pb2 import RateLimit, Status  # type: ignore[import-untyped]

from zhiheng.modules.knowledge.control import VectorIOControl
from zhiheng.shared.errors import AppError


def _rpc_result(control: VectorIOControl, future: Any) -> Any:
    try:
        while True:
            try:
                result = future.result(timeout=min(0.1, control.remaining()))
                break
            except grpc.FutureTimeoutError:
                continue
        control.check()
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
        return _rpc_result(self._control, future), future

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
