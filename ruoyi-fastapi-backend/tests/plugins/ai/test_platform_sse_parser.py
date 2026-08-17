from collections.abc import Mapping

import pytest

from plugins.ai.infrastructure.platform_sse import (
    PlatformSSEError,
    PlatformSSEEvent,
    parse_platform_sse,
)

PRIVATE_BODY = 'PRIVATE-UPSTREAM-BODY'


class ClosableChunks:
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self._index = 0
        self.close_count = 0

    def __aiter__(self) -> 'ClosableChunks':
        return self

    async def __anext__(self) -> bytes:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    async def aclose(self) -> None:
        self.close_count += 1


async def _events(*chunks: bytes) -> list[PlatformSSEEvent]:
    return [event async for event in parse_platform_sse(ClosableChunks(chunks))]


@pytest.mark.asyncio
async def test_parser_handles_utf8_and_json_split_across_arbitrary_chunks() -> None:
    encoded = 'event: content\ndata: {"delta":"你"}\n\n'.encode()
    character_start = encoded.index('你'.encode())

    events = await _events(
        encoded[:7],
        encoded[7 : character_start + 1],
        encoded[character_start + 1 : character_start + 2],
        encoded[character_start + 2 : -1],
        encoded[-1:],
    )

    assert events == [PlatformSSEEvent(event='content', payload={'delta': '你'})]


@pytest.mark.asyncio
async def test_parser_handles_multiple_events_in_one_chunk_and_done() -> None:
    events = await _events(
        b': keepalive\n\n'
        b'event: content\ndata: {"delta":"one"}\n\n'
        b'event: usage\ndata: {"prompt_tokens":1,"completion_tokens":2}\n\n'
        b'data: [DONE]\n\n'
    )

    assert events == [
        PlatformSSEEvent(event='content', payload={'delta': 'one'}),
        PlatformSSEEvent(
            event='usage',
            payload={'prompt_tokens': 1, 'completion_tokens': 2},
        ),
        PlatformSSEEvent(event='message', payload=None, done=True),
    ]


@pytest.mark.asyncio
async def test_parser_joins_multiple_data_lines_and_accepts_crlf_boundaries() -> None:
    events = await _events(b'event: content\r\ndata: {"delta":\r\ndata: "joined"}\r\n\r\n')

    assert events == [PlatformSSEEvent(event='content', payload={'delta': 'joined'})]


@pytest.mark.asyncio
async def test_parser_ignores_unknown_fields_comments_and_empty_blocks() -> None:
    events = await _events(
        b'\n: ping\nretry: 1000\nid: internal-id\n\nevent: meta\nunknown: private\ndata: {"id":"call-1"}\n\n'
    )

    assert events == [PlatformSSEEvent(event='meta', payload={'id': 'call-1'})]


@pytest.mark.asyncio
async def test_parser_dispatches_final_event_without_trailing_blank_line() -> None:
    events = await _events(b'data: {"delta":"tail"}')

    assert events == [PlatformSSEEvent(event='message', payload={'delta': 'tail'})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    (
        f'data: {{"secret":"{PRIVATE_BODY}"\n\n'.encode(),
        b'data: [1, 2, 3]\n\n',
        b'data: "scalar"\n\n',
        b'data: \xff\n\n',
    ),
)
async def test_parser_rejects_invalid_json_or_utf8_without_retaining_body(body: bytes) -> None:
    with pytest.raises(PlatformSSEError) as raised:
        await _events(body)

    error = raised.value
    assert error.args == ()
    assert str(error) == '模型流响应格式无效'
    assert PRIVATE_BODY not in repr(error)
    assert PRIVATE_BODY not in repr(vars(error))
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.asyncio
async def test_downstream_close_closes_upstream_iterator_once() -> None:
    source = ClosableChunks(
        (
            b'data: {"delta":"first"}\n\n',
            b'data: {"delta":"second"}\n\n',
        )
    )
    stream = parse_platform_sse(source)

    assert await anext(stream) == PlatformSSEEvent(event='message', payload={'delta': 'first'})
    await stream.aclose()
    await stream.aclose()

    assert source.close_count == 1


def test_sse_event_copies_payload_and_rejects_non_mapping_payload() -> None:
    payload: dict[str, object] = {'nested': {'value': 'original'}, 'items': [1, 2]}
    event = PlatformSSEEvent(event='message', payload=payload)
    nested = payload['nested']
    assert isinstance(nested, dict)
    nested['value'] = 'mutated'

    assert isinstance(event.payload, Mapping)
    assert event.payload['nested'] == {'value': 'original'}
    with pytest.raises(TypeError):
        PlatformSSEEvent(event='message', payload=['not-a-mapping'])  # type: ignore[arg-type]
