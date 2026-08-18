import asyncio
import json
from collections.abc import AsyncIterator

from plugins.ai.domain.model_runtime import ModelStreamEvent
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.vo.ai_openai_compat_vo import OpenAIChatCompletionChunkModel

_DONE_FRAME = b'data: [DONE]\n\n'


class OpenAIStreamAdapter:
    """Convert normalized runtime events to an OpenAI-compatible SSE stream."""

    def __init__(
        self,
        *,
        completion_id: str,
        created: int,
        model_code: str,
        stream: AsyncIterator[ModelStreamEvent],
    ) -> None:
        self._completion_id = completion_id
        self._created = created
        self._model_code = model_code
        self._iterator = stream.__aiter__()
        self._operation_lock = asyncio.Lock()
        self._phase = 'role'
        self._terminal = False
        self._source_closed = False
        self._normal_close_failed = False
        self._role_frame = self._encode_chunk({'role': 'assistant'}, None)
        self._finish_frame = self._encode_chunk({}, 'stop')

    def __aiter__(self) -> 'OpenAIStreamAdapter':
        return self

    async def __anext__(self) -> bytes:
        async with self._operation_lock:
            if self._terminal:
                raise StopAsyncIteration
            if self._phase == 'role':
                self._phase = 'events'
                return self._role_frame
            if self._phase == 'done-marker':
                self._phase = 'exhausted'
                self._terminal = True
                return _DONE_FRAME
            try:
                return await self._next_runtime_frame_locked()
            except BaseException:
                self._terminal = True
                if self._normal_close_failed:
                    self._normal_close_failed = False
                else:
                    await self._close_preserving_active_error()
                raise

    async def aclose(self) -> None:
        async with self._operation_lock:
            if self._source_closed:
                self._terminal = True
                return
            await self._close_source_locked()
            self._terminal = True

    async def _next_runtime_frame_locked(self) -> bytes:
        while True:
            try:
                event = await anext(self._iterator)
            except StopAsyncIteration:
                await self._close_source_locked()
                raise ModelServiceError.runtime_error() from None
            if event.event_type == 'content' and event.delta:
                return self._encode_chunk({'content': event.delta}, None)
            if event.event_type == 'done':
                try:
                    await self._close_source_locked()
                except BaseException:
                    self._normal_close_failed = True
                    raise
                self._phase = 'done-marker'
                return self._finish_frame

    async def _close_source_locked(self) -> None:
        if self._source_closed:
            return
        close = getattr(self._iterator, 'aclose', None)
        if close is not None:
            await close()
        self._source_closed = True

    async def _close_preserving_active_error(self) -> None:
        try:
            await self._close_source_locked()
        except BaseException:
            pass

    def _encode_chunk(self, delta: dict[str, str], finish_reason: str | None) -> bytes:
        chunk = OpenAIChatCompletionChunkModel.model_validate(
            {
                'id': self._completion_id,
                'object': 'chat.completion.chunk',
                'created': self._created,
                'model': self._model_code,
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}],
            }
        )
        payload = chunk.model_dump(exclude_none=True)
        if finish_reason is None:
            payload['choices'][0]['finish_reason'] = None
        encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()
        return b'data: ' + encoded + b'\n\n'
