import ast
import asyncio
import json
from collections.abc import AsyncIterator
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ConfigDict
from starlette.requests import Request

from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity
from plugins.ai.public import api_transport

MAX_BYTES = 64
VALID_BODY = b'{"message":"hello"}'
JSON_HEADERS = [(b'content-type', b'application/json')]


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str


def make_request(
    headers: list[tuple[bytes, bytes]],
    chunks: tuple[bytes, ...] = (VALID_BODY,),
    *,
    failure: BaseException | None = None,
) -> tuple[Request, list[bytes]]:
    remaining = iter(chunks)
    reads: list[bytes] = []

    async def receive() -> dict[str, object]:
        if failure is not None:
            raise failure
        chunk = next(remaining, b'')
        reads.append(chunk)
        return {'type': 'http.request', 'body': chunk, 'more_body': bool(chunk)}

    return Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': headers}, receive), reads


@pytest.mark.asyncio
@pytest.mark.parametrize('duplicate', [False, True])
async def test_authentication_accepts_only_one_header_without_forwarding_duplicate_keys(duplicate: bool) -> None:
    headers = [(b'Authorization', b'Bearer test-key')]
    if duplicate:
        headers.append((b'authorization', b'Bearer other-key'))
    request, _ = make_request(headers)
    identity = DeveloperApiIdentity(application_id='app-test', key_id='key-test')
    authenticator = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=SimpleNamespace(
                identity=None if duplicate else identity, failure='INVALID', base_exception_type=None
            )
        )
    )
    if duplicate:
        with pytest.raises(PublicApiError) as raised:
            await api_transport.authenticate_request(request, authenticator)
        assert raised.value.public_dict() == PublicApiError.invalid_api_key().public_dict()
    else:
        assert await api_transport.authenticate_request(request, authenticator) is identity
    assert authenticator.authenticate.await_args.args == (None if duplicate else 'Bearer test-key',)
    assert authenticator.authenticate.await_args.kwargs['now'].tzinfo is not None


@pytest.mark.asyncio
async def test_json_reader_preserves_schema_and_accepts_exact_byte_limit() -> None:
    request, _ = make_request(
        [(b'content-type', b'Application/JSON; charset=utf-8'), (b'content-length', str(len(VALID_BODY)).encode())],
        (VALID_BODY[:4], VALID_BODY[4:]),
    )
    outcome = await api_transport.read_json_request(request, len(VALID_BODY), Payload)
    assert isinstance(outcome.value, Payload)
    assert outcome.value.message == 'hello'
    assert not outcome.cancelled


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('headers', 'body'),
    [
        ([], VALID_BODY),
        ([(b'content-type', b'text/plain')], VALID_BODY),
        (JSON_HEADERS * 2, VALID_BODY),
        ([*JSON_HEADERS, (b'content-length', b'1'), (b'Content-Length', b'1')], VALID_BODY),
        ([*JSON_HEADERS, (b'content-length', b'invalid')], VALID_BODY),
        ([*JSON_HEADERS, (b'content-length', b'-1')], VALID_BODY),
        ([*JSON_HEADERS, (b'content-length', b'1')], VALID_BODY),
        ([*JSON_HEADERS, (b'content-length', b'999999')], VALID_BODY),
        (JSON_HEADERS, b'{not-json'),
        (JSON_HEADERS, b'{"message":"hello","unknown":true}'),
    ],
)
async def test_invalid_json_metadata_or_schema_has_no_validated_value(
    headers: list[tuple[bytes, bytes]], body: bytes
) -> None:
    request, _ = make_request(headers, (body,))
    outcome = await api_transport.read_json_request(request, MAX_BYTES, Payload)
    assert outcome.value is None
    assert not outcome.cancelled


@pytest.mark.asyncio
async def test_chunked_body_limit_stops_consuming_on_first_excess_chunk() -> None:
    first, excess, unread = b'{' * MAX_BYTES, b'!', b'must-not-be-consumed'
    request, reads = make_request(JSON_HEADERS, (first, excess, unread))
    outcome = await api_transport.read_json_request(request, MAX_BYTES, Payload)
    assert outcome.value is None
    assert reads == [first, excess]


@pytest.mark.asyncio
async def test_body_cancellation_is_distinct_from_invalid_request() -> None:
    request, _ = make_request(JSON_HEADERS, failure=asyncio.CancelledError())
    outcome = await api_transport.read_json_request(request, MAX_BYTES, Payload)
    assert outcome.cancelled
    assert outcome.value is None


@pytest.mark.parametrize('unauthorized', [False, True])
def test_public_error_response_keeps_exact_body_and_bearer_challenge(unauthorized: bool) -> None:
    error = PublicApiError.invalid_api_key() if unauthorized else PublicApiError.service_unavailable()
    response = api_transport.error_response(error)
    assert response.status_code == error.status_code
    assert json.loads(response.body) == error.public_dict()
    if unauthorized:
        assert response.status_code == HTTPStatus.UNAUTHORIZED
        assert response.headers['www-authenticate'] == 'Bearer'
    else:
        assert 'www-authenticate' not in response.headers


class ByteStream:
    def __init__(self, *, failure: BaseException | None = None, close_failure: BaseException | None = None) -> None:
        self.frames = iter((b'data: {"content":"hello"}\n\n', b'data: [DONE]\n\n'))
        self.failure = failure
        self.close_failure = close_failure
        self.close_count = 0

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        if self.failure is not None:
            raise self.failure
        try:
            return next(self.frames)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.close_count += 1
        if self.close_failure is not None:
            raise self.close_failure


@pytest.mark.asyncio
async def test_sse_response_preserves_frames_headers_and_closes_once() -> None:
    upstream = ByteStream()
    response = api_transport.streaming_response(upstream)
    assert response.media_type == 'text/event-stream'
    assert response.headers['cache-control'] == 'no-cache'
    assert response.headers['x-accel-buffering'] == 'no'
    assert [frame async for frame in response.body_iterator] == [b'data: {"content":"hello"}\n\n', b'data: [DONE]\n\n']
    assert upstream.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('cancelled', [False, True])
async def test_sse_preserves_read_failure_or_cancellation_over_cleanup_error(cancelled: bool) -> None:
    failure = asyncio.CancelledError() if cancelled else RuntimeError('read failed')
    upstream = ByteStream(failure=failure, close_failure=RuntimeError('close failed'))
    response = api_transport.streaming_response(upstream)
    with pytest.raises(type(failure)) as raised:
        await anext(response.body_iterator)
    assert raised.value is failure
    assert upstream.close_count == 1


@pytest.mark.asyncio
async def test_early_sse_close_releases_stream_without_emitting_more_frames() -> None:
    upstream = ByteStream()
    response = api_transport.streaming_response(upstream)
    assert await anext(response.body_iterator) == b'data: {"content":"hello"}\n\n'
    await response.body_iterator.aclose()
    assert upstream.close_count == 1
    assert [frame async for frame in response.body_iterator] == []


@pytest.mark.asyncio
async def test_sse_success_does_not_swallow_cleanup_failure() -> None:
    failure = RuntimeError('close failed')
    upstream = ByteStream(close_failure=failure)
    response = api_transport.streaming_response(upstream)
    with pytest.raises(RuntimeError) as raised:
        _ = [frame async for frame in response.body_iterator]
    assert raised.value is failure
    assert upstream.close_count == 1


def test_agent_and_public_transport_do_not_import_ai_controllers() -> None:
    root = Path(__file__).resolve().parents[3]
    files = list((root / 'plugins/agent_extension').rglob('*.py'))
    public_file = root / 'plugins/ai/public/api_transport.py'
    violations = []
    for path in [*files, public_file]:
        forbidden = ['plugins.ai.controller']
        if path == public_file:
            forbidden += ['plugins.ai.dao', 'config.database', 'config.get_db', 'sqlalchemy']
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f'{node.module}.{item.name}' for item in node.names)]
            if any(name == prefix or name.startswith(prefix + '.') for name in names for prefix in forbidden):
                violations.append(f'{path.relative_to(root)}:{node.lineno}')
    assert not violations, 'Consumers must use the public transport boundary: ' + ', '.join(violations)
