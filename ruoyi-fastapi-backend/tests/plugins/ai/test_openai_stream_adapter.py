import asyncio
import json
from collections.abc import Sequence

import pytest

from plugins.ai.domain.model_runtime import ModelStreamEvent, ModelUsage
from plugins.ai.infrastructure.openai_stream_adapter import OpenAIStreamAdapter

MODEL_CODE = 'deepseek-chat'
COMPLETION_ID = 'chatcmpl_00000000000040008000000000000001'
CREATED = 1786982400
TWO_CLOSE_ATTEMPTS = 2


class FakeStream:
    def __init__(
        self,
        events: Sequence[ModelStreamEvent] = (),
        *,
        error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self._events = iter(events)
        self._error = error
        self._close_error = close_error
        self.read_count = 0
        self.close_count = 0

    def __aiter__(self) -> 'FakeStream':
        return self

    async def __anext__(self) -> ModelStreamEvent:
        self.read_count += 1
        if self._error is not None:
            error = self._error
            self._error = None
            raise error
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.close_count += 1
        if self._close_error is not None:
            error = self._close_error
            self._close_error = None
            raise error


class CoordinatedStream:
    def __init__(self) -> None:
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()
        self.close_count = 0

    def __aiter__(self) -> 'CoordinatedStream':
        return self

    async def __anext__(self) -> ModelStreamEvent:
        self.read_started.set()
        await self.release_read.wait()
        return ModelStreamEvent(event_type='content', delta='one')

    async def aclose(self) -> None:
        self.close_count += 1


def adapter(stream: object) -> OpenAIStreamAdapter:
    return OpenAIStreamAdapter(
        completion_id=COMPLETION_ID,
        created=CREATED,
        model_code=MODEL_CODE,
        stream=stream,  # type: ignore[arg-type]
    )


def _sse(payload: dict[str, object]) -> bytes:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
    return b'data: ' + encoded + b'\n\n'


def _chunk(delta: dict[str, str], finish_reason: str | None) -> dict[str, object]:
    return {
        'choices': [{'delta': delta, 'finish_reason': finish_reason, 'index': 0}],
        'created': CREATED,
        'id': COMPLETION_ID,
        'model': MODEL_CODE,
        'object': 'chat.completion.chunk',
    }


@pytest.mark.asyncio
async def test_stream_emits_exact_role_content_finish_and_done_sequence() -> None:
    source = FakeStream(
        (
            ModelStreamEvent(event_type='meta', request_id='PRIVATE-request-id'),
            ModelStreamEvent(event_type='reasoning', delta='PRIVATE reasoning'),
            ModelStreamEvent(event_type='content', delta='你'),
            ModelStreamEvent(event_type='usage', usage=ModelUsage(input_tokens=2, output_tokens=2)),
            ModelStreamEvent(event_type='content', delta='好'),
            ModelStreamEvent(event_type='done'),
        )
    )

    frames = [frame async for frame in adapter(source)]

    assert frames == [
        _sse(_chunk({'role': 'assistant'}, None)),
        _sse(_chunk({'content': '你'}, None)),
        _sse(_chunk({'content': '好'}, None)),
        _sse(_chunk({}, 'stop')),
        b'data: [DONE]\n\n',
    ]
    assert source.close_count == 1
    joined = b''.join(frames)
    assert b'PRIVATE' not in joined


@pytest.mark.asyncio
async def test_first_role_frame_does_not_read_upstream_and_consumer_controls_reads() -> None:
    source = FakeStream((ModelStreamEvent(event_type='content', delta='one'),))
    stream = adapter(source)

    assert await anext(stream) == _sse(_chunk({'role': 'assistant'}, None))
    assert source.read_count == 0
    assert await anext(stream) == _sse(_chunk({'content': 'one'}, None))
    assert source.read_count == 1
    await stream.aclose()


@pytest.mark.asyncio
async def test_early_close_is_idempotent_and_does_not_emit_done() -> None:
    source = FakeStream((ModelStreamEvent(event_type='content', delta='one'),))
    stream = adapter(source)

    assert await anext(stream) == _sse(_chunk({'role': 'assistant'}, None))
    await stream.aclose()
    await stream.aclose()

    assert source.close_count == 1
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_concurrent_close_waits_for_pending_read_then_closes_once() -> None:
    source = CoordinatedStream()
    stream = adapter(source)
    await anext(stream)

    pending_read = asyncio.create_task(anext(stream))
    await source.read_started.wait()
    pending_close = asyncio.create_task(stream.aclose())
    await asyncio.sleep(0)
    assert pending_close.done() is False

    source.release_read.set()
    assert await pending_read == _sse(_chunk({'content': 'one'}, None))
    await pending_close

    assert source.close_count == 1


@pytest.mark.asyncio
async def test_runtime_error_closes_once_and_does_not_turn_into_success() -> None:
    runtime_error = RuntimeError('stable runtime error')
    source = FakeStream(error=runtime_error)
    stream = adapter(source)
    await anext(stream)

    with pytest.raises(RuntimeError) as raised:
        await anext(stream)

    assert raised.value is runtime_error
    assert source.close_count == 1
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


@pytest.mark.asyncio
async def test_cancellation_wins_over_close_error() -> None:
    source = FakeStream(error=asyncio.CancelledError(), close_error=RuntimeError('close failed'))
    stream = adapter(source)
    await anext(stream)

    with pytest.raises(asyncio.CancelledError):
        await anext(stream)

    assert source.close_count == 1


@pytest.mark.asyncio
async def test_normal_done_close_error_is_visible_before_finish_and_can_be_retried() -> None:
    close_error = RuntimeError('close failed')
    source = FakeStream((ModelStreamEvent(event_type='done'),), close_error=close_error)
    stream = adapter(source)
    await anext(stream)

    with pytest.raises(RuntimeError) as raised:
        await anext(stream)

    assert raised.value is close_error
    assert source.close_count == 1
    await stream.aclose()
    assert source.close_count == TWO_CLOSE_ATTEMPTS
