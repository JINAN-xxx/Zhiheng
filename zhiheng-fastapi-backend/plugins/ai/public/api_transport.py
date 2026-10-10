"""Shared HTTP transport for public model and Agent calls.

Callers own feature flags, service construction and business authorization.
This module preserves the public API's request validation and response lifecycle.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from typing import Generic, Protocol, TypeVar

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError
from starlette.types import Receive, Scope, Send

from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.infrastructure.developer_key_authenticator import (
    DeveloperApiIdentity,
    DeveloperKeyAuthenticator,
    require_authenticated_identity,
)

BodyT = TypeVar('BodyT', bound=BaseModel)
_INVALID_HEADER = object()
OPENAI_SSE_MEDIA_TYPE = 'text/event-stream'
_OPENAI_SSE_HEADERS = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}


@dataclass(frozen=True, slots=True)
class RequestBody(Generic[BodyT]):
    """Validated body or cancellation outcome, without retaining raw body bytes."""

    value: BodyT | None = None
    cancelled: bool = False


class ByteStream(Protocol):
    """A byte stream whose resources are released when its HTTP response ends."""

    def __aiter__(self) -> AsyncIterator[bytes]: ...

    async def aclose(self) -> None: ...


class CloseableStream(Protocol):
    """An owned SSE source, including serializers that yield text frames."""

    def __aiter__(self) -> AsyncIterator[bytes | str]: ...

    async def aclose(self) -> None: ...


class _ManagedStream:
    def __init__(self, stream: CloseableStream) -> None:
        self._stream = stream
        self._iterator = stream.__aiter__()
        self._lock = asyncio.Lock()
        self._closed = False

    def __aiter__(self) -> '_ManagedStream':
        return self

    async def __anext__(self) -> bytes | str:
        async with self._lock:
            if self._closed:
                raise StopAsyncIteration
            try:
                return await anext(self._iterator)
            except StopAsyncIteration:
                await self._close_locked()
                raise
            except BaseException:
                try:
                    await self._close_locked()
                except BaseException:
                    pass
                raise

    async def aclose(self) -> None:
        with anyio.CancelScope(shield=True):
            async with self._lock:
                await self._close_locked()

    async def _close_locked(self) -> None:
        if self._closed:
            return
        with anyio.CancelScope(shield=True):
            await self._stream.aclose()
            self._closed = True


class ManagedStreamingResponse(StreamingResponse):
    """Own a closeable stream even if sending headers fails before iteration."""

    def __init__(
        self,
        stream: CloseableStream,
        *,
        media_type: str,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._owned_stream = _ManagedStream(stream)
        super().__init__(self._owned_stream, media_type=media_type, headers=headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        active_error: BaseException | None = None
        try:
            await super().__call__(scope, receive, send)
        except BaseException as error:
            active_error = error
            raise
        finally:
            try:
                await self._owned_stream.aclose()
            except BaseException:
                if active_error is None:
                    raise


async def authenticate_request(request: Request, authenticator: DeveloperKeyAuthenticator) -> DeveloperApiIdentity:
    """Authenticate one Authorization header; duplicate headers fail closed."""
    authorization = _single_authorization(request)
    outcome = await authenticator.authenticate(authorization, now=datetime.now(timezone.utc))
    del authorization
    return require_authenticated_identity(outcome)


async def read_json_request(request: Request, max_bytes: int, model_type: type[BodyT]) -> RequestBody[BodyT]:
    """Validate JSON metadata, streamed byte limits and the caller's schema.

    Invalid input returns an empty result. Cancellation is returned separately so
    callers can release request state before propagating cancellation.
    """
    try:
        content_type = _single_header(request, b'content-type')
        if content_type is _INVALID_HEADER or not isinstance(content_type, str):
            return RequestBody()
        if content_type.split(';', 1)[0].strip().lower() != 'application/json':
            return RequestBody()
        content_length = _content_length(request)
        if content_length is _INVALID_HEADER or (isinstance(content_length, int) and content_length > max_bytes):
            return RequestBody()
        body = bytearray()
        async for chunk in request.stream():
            if not isinstance(chunk, bytes) or len(chunk) > max_bytes - len(body):
                return RequestBody()
            body.extend(chunk)
        if isinstance(content_length, int) and content_length != len(body):
            return RequestBody()
        payload = json.loads(body)
        value = model_type.model_validate(payload)
        return RequestBody(value=value)
    except asyncio.CancelledError:
        return RequestBody(cancelled=True)
    except (UnicodeError, ValueError, TypeError, ValidationError):
        return RequestBody()
    except Exception:
        return RequestBody()


def error_response(error: PublicApiError) -> JSONResponse:
    """Return the fixed public error envelope and the 401 Bearer challenge."""
    headers = {'WWW-Authenticate': 'Bearer'} if error.status_code == HTTPStatus.UNAUTHORIZED else None
    return JSONResponse(status_code=error.status_code, content=error.public_dict(), headers=headers)


def streaming_response(stream: ByteStream) -> StreamingResponse:
    """Forward SSE bytes unchanged and own cleanup for the whole HTTP response."""
    return ManagedStreamingResponse(stream, media_type=OPENAI_SSE_MEDIA_TYPE, headers=_OPENAI_SSE_HEADERS)


def _single_authorization(request: Request) -> str | None:
    values = _raw_header_values(request, b'authorization')
    if len(values) != 1:
        return None
    try:
        return values[0].decode('latin-1')
    except Exception:
        return None


def _content_length(request: Request) -> int | object | None:
    value = _single_header(request, b'content-length')
    if value is None or value is _INVALID_HEADER:
        return value
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        return _INVALID_HEADER
    return int(value)


def _single_header(request: Request, name: bytes) -> str | object | None:
    values = _raw_header_values(request, name)
    if not values:
        return None
    if len(values) != 1:
        return _INVALID_HEADER
    try:
        return values[0].decode('ascii')
    except UnicodeDecodeError:
        return _INVALID_HEADER


def _raw_header_values(request: Request, name: bytes) -> list[bytes]:
    headers = request.scope.get('headers', ())
    return [value for raw_name, value in headers if raw_name.lower() == name]
