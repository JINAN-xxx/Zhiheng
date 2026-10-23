"""Preserve provider counter presence before LangChain can synthesize zero values."""

from collections.abc import AsyncIterator, Awaitable, Callable
from types import TracebackType
from typing import cast

from openai import AsyncStream
from openai.resources.chat.completions import AsyncCompletions
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletionChunk

from zhiheng.modules.models.contracts import ProviderError


class UsageCapture:
    def __init__(self) -> None:
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None

    def observe(self, usage: CompletionUsage) -> None:
        prompt: object = usage.prompt_tokens if 'prompt_tokens' in usage.model_fields_set else None
        completion: object = usage.completion_tokens if 'completion_tokens' in usage.model_fields_set else None
        self.input_tokens = prompt if type(prompt) is int and prompt >= 0 else None
        self.output_tokens = completion if type(completion) is int and completion >= 0 else None


class CapturingStream:
    def __init__(self, source: AsyncStream[ChatCompletionChunk], usage: UsageCapture) -> None:
        self._source, self._usage = source, usage

    async def __aenter__(self) -> 'CapturingStream':
        await self._source.__aenter__()
        return self

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._source.__aexit__(exception_type, exception, traceback)

    async def __aiter__(self) -> AsyncIterator[ChatCompletionChunk]:
        async for chunk in self._source:
            if chunk.usage is not None:
                self._usage.observe(chunk.usage)
            yield chunk


class CapturingCompletions:
    def __init__(self, source: AsyncCompletions, usage: UsageCapture) -> None:
        # The streaming branch of the public SDK overload is fixed by create().
        self._create = cast('Callable[..., Awaitable[AsyncStream[ChatCompletionChunk]]]', source.create)
        self._usage = usage

    async def create(self, **kwargs: object) -> CapturingStream:
        if kwargs.get('stream') is not True:
            raise ProviderError
        return CapturingStream(await self._create(**kwargs), self._usage)
