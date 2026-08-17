import json
import re
import sys
from codecs import getincrementaldecoder
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import NoReturn

_MAX_EVENT_CHARACTERS = 1_048_576
_EVENT_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


class PlatformSSEError(Exception):
    """不携带上游正文的固定 SSE 解析错误。"""

    __slots__ = ()

    def __init__(self) -> None:
        super().__init__()

    def __str__(self) -> str:
        return '模型流响应格式无效'

    def __repr__(self) -> str:
        return 'PlatformSSEError()'


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise TypeError('SSE JSON 包含不支持的值')


@dataclass(frozen=True, slots=True)
class PlatformSSEEvent:
    event: str
    payload: Mapping[str, object] | None
    done: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.event, str) or _EVENT_NAME_PATTERN.fullmatch(self.event) is None:
            raise ValueError('SSE 事件名称无效')
        if self.done:
            if self.payload is not None:
                raise ValueError('SSE 完成事件不能携带数据')
            return
        if not isinstance(self.payload, Mapping):
            raise TypeError('SSE 事件数据必须是 JSON 对象')
        object.__setattr__(self, 'payload', _freeze_json(self.payload))


@dataclass(frozen=True, slots=True)
class _PayloadOutcome:
    failed: bool
    payload: Mapping[str, object] | None = None


def _decode_json_payload(data: str) -> _PayloadOutcome:
    try:
        decoded = json.loads(data)
        if not isinstance(decoded, Mapping):
            return _PayloadOutcome(failed=True)
        frozen = _freeze_json(decoded)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _PayloadOutcome(failed=True)
    if not isinstance(frozen, Mapping):
        return _PayloadOutcome(failed=True)
    return _PayloadOutcome(failed=False, payload=frozen)


def _raise_invalid_sse() -> NoReturn:
    raise PlatformSSEError


def _pop_line(buffer: str, *, final: bool) -> tuple[str | None, str]:
    for index, character in enumerate(buffer):
        if character == '\n':
            return buffer[:index], buffer[index + 1 :]
        if character == '\r':
            if index + 1 == len(buffer) and not final:
                return None, buffer
            consumed = index + 2 if buffer[index + 1 : index + 2] == '\n' else index + 1
            return buffer[:index], buffer[consumed:]
    if final and buffer:
        return buffer, ''
    return None, buffer


@dataclass(frozen=True, slots=True)
class _LineOutcome:
    failed: bool = False
    event: PlatformSSEEvent | None = None


class _SSEParser:
    __slots__ = ('_buffer', '_data_lines', '_decoder', '_event_name')

    def __init__(self) -> None:
        self._decoder = getincrementaldecoder('utf-8')('strict')
        self._buffer = ''
        self._event_name = 'message'
        self._data_lines: list[str] = []

    def feed(self, raw_chunk: object) -> list[PlatformSSEEvent] | None:
        if not isinstance(raw_chunk, (bytes, bytearray)):
            return None
        try:
            decoded = self._decoder.decode(bytes(raw_chunk), final=False)
        except UnicodeDecodeError:
            self.clear()
            return None
        return self._consume(decoded, final=False)

    def finish(self) -> list[PlatformSSEEvent] | None:
        try:
            decoded = self._decoder.decode(b'', final=True)
        except UnicodeDecodeError:
            self.clear()
            return None
        events = self._consume(decoded, final=True)
        if events is None:
            return None
        outcome = self._dispatch()
        if outcome.failed:
            return None
        if outcome.event is not None:
            events.append(outcome.event)
        return events

    def clear(self) -> None:
        self._buffer = ''
        self._event_name = 'message'
        self._data_lines = []

    def _consume(self, decoded: str, *, final: bool) -> list[PlatformSSEEvent] | None:
        self._buffer += decoded
        if len(self._buffer) > _MAX_EVENT_CHARACTERS:
            self.clear()
            return None
        events: list[PlatformSSEEvent] = []
        while True:
            line, self._buffer = _pop_line(self._buffer, final=final)
            if line is None:
                return events
            outcome = self._process_line(line)
            if outcome.failed:
                self.clear()
                return None
            if outcome.event is not None:
                events.append(outcome.event)

    def _process_line(self, line: str) -> _LineOutcome:
        if line == '':
            return self._dispatch()
        if line.startswith(':'):
            return _LineOutcome()
        field, separator, value = line.partition(':')
        if separator and value.startswith(' '):
            value = value[1:]
        if field == 'event':
            if _EVENT_NAME_PATTERN.fullmatch(value) is None:
                return _LineOutcome(failed=True)
            self._event_name = value
        elif field == 'data':
            self._data_lines.append(value)
            if sum(len(item) for item in self._data_lines) > _MAX_EVENT_CHARACTERS:
                return _LineOutcome(failed=True)
        return _LineOutcome()

    def _dispatch(self) -> _LineOutcome:
        if not self._data_lines:
            self._event_name = 'message'
            return _LineOutcome()
        data = '\n'.join(self._data_lines)
        current_event = self._event_name
        self._event_name = 'message'
        self._data_lines = []
        if data == '[DONE]':
            return _LineOutcome(event=PlatformSSEEvent(event=current_event, payload=None, done=True))
        outcome = _decode_json_payload(data)
        if outcome.failed or outcome.payload is None:
            return _LineOutcome(failed=True)
        return _LineOutcome(event=PlatformSSEEvent(event=current_event, payload=outcome.payload))


async def _close_source(source: AsyncIterator[bytes], *, preserve_active_error: bool) -> None:
    close = getattr(source, 'aclose', None)
    if close is None:
        return
    if not preserve_active_error:
        await close()
        return
    try:
        await close()
    except BaseException:
        pass


async def parse_platform_sse(source: AsyncIterator[bytes]) -> AsyncIterator[PlatformSSEEvent]:
    """将任意字节分片的 SSE 流解析为不可变 JSON 事件。"""

    parser = _SSEParser()
    source_iterator = source.__aiter__()
    try:
        async for raw_chunk in source_iterator:
            events = parser.feed(raw_chunk)
            del raw_chunk
            if events is None:
                _raise_invalid_sse()
            for event in events:
                yield event
        events = parser.finish()
        if events is None:
            _raise_invalid_sse()
        for event in events:
            yield event
    finally:
        parser.clear()
        await _close_source(source_iterator, preserve_active_error=sys.exc_info()[0] is not None)
