"""Capture raw JSON counter types before SDK or LangChain can synthesize/coerce zero."""

from collections.abc import AsyncIterator, Mapping
from types import TracebackType

from openai import AsyncOpenAI, AsyncStream

from zhiheng.modules.models.contracts import ProviderError


def normalize_counter(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


class UsageCapture:
    def __init__(self) -> None:
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cache_tokens: int | None = None
        self.reasoning_tokens: int | None = None

    def observe_raw(self, usage: Mapping[str, object]) -> None:
        self.input_tokens = normalize_counter(usage.get('prompt_tokens'))
        self.output_tokens = normalize_counter(usage.get('completion_tokens'))
        cache_details = usage.get('prompt_tokens_details')
        reasoning_details = usage.get('completion_tokens_details')
        self.cache_tokens = (
            normalize_counter(cache_details.get('cached_tokens')) if isinstance(cache_details, dict) else None
        )
        self.reasoning_tokens = (
            normalize_counter(reasoning_details.get('reasoning_tokens'))
            if isinstance(reasoning_details, dict)
            else None
        )
        if isinstance(cache_details, dict) and 'cached_tokens' in cache_details and self.cache_tokens is None:
            self.input_tokens = None
        if self.cache_tokens is not None and self.input_tokens is not None and self.cache_tokens > self.input_tokens:
            self.input_tokens = None


class CapturingStream:
    def __init__(self, source: AsyncStream[dict[str, object]], usage: UsageCapture) -> None:
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

    async def __aiter__(self) -> AsyncIterator[dict[str, object]]:
        async for chunk in self._source:
            usage = chunk.get('usage')
            if isinstance(usage, dict):
                self._usage.observe_raw(usage)
            yield chunk


class CapturingCompletions:
    def __init__(self, source: AsyncOpenAI, usage: UsageCapture) -> None:
        self._source, self._usage = source, usage

    async def create(self, **kwargs: object) -> CapturingStream:
        if kwargs.get('stream') is not True:
            raise ProviderError
        # The public SDK ChatCompletionChunk.usage union can coerce bool/string/
        # float into int before capture. Request raw JSON dictionaries through
        # the same isolated SDK client; LangChain accepts these wire chunks.
        stream = await self._source.post(
            '/chat/completions',
            # A runtime class avoids GenericAlias/issubclass incompatibility in
            # the SDK's protocol check on Python 3.10.
            cast_to=dict,
            body=kwargs,
            stream=True,
            # The SDK derives its cast from this runtime parameter as well.
            stream_cls=AsyncStream[dict],  # type: ignore[type-arg]
        )
        return CapturingStream(stream, self._usage)
