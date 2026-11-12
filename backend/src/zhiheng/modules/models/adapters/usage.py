"""Capture raw JSON counter types before SDK or LangChain can synthesize/coerce zero."""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from types import TracebackType

from openai import AsyncOpenAI, AsyncStream

from zhiheng.modules.models.contracts import ProviderError


def normalize_counter(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


@dataclass(frozen=True)
class UsageCounters:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class _Counter:
    value: int | None = None
    invalid: bool = False


def _counter(sources: tuple[Mapping[str, object], ...], *paths: tuple[str, ...]) -> _Counter:
    values: set[int] = set()
    for source in sources:
        for path in paths:
            value: object = source
            for key in path:
                if value is None:
                    break
                if not isinstance(value, Mapping):
                    return _Counter(invalid=True)
                if key not in value:
                    break
                value = value[key]
            else:
                count = normalize_counter(value)
                if count is None:
                    return _Counter(invalid=True)
                values.add(count)
    return _Counter(next(iter(values))) if len(values) == 1 else _Counter(invalid=bool(values))


def normalize_usage(*sources: Mapping[str, object]) -> UsageCounters:
    """Normalize reported counts; never derive missing facts from totals or text.

    OpenAI-compatible endpoints share these fields across domestic providers.
    DeepSeek also reports cache hits/misses; Kimi exposes cached_tokens at the
    top level. Compatible custom relays may use input_tokens/output_tokens.
    Every supplied alias must agree, including aliases in rerank meta.tokens.
    """
    prompt = _counter(sources, ('prompt_tokens',), ('input_tokens',))
    completion = _counter(sources, ('completion_tokens',), ('output_tokens',))
    cached = _counter(
        sources,
        ('prompt_tokens_details', 'cached_tokens'),
        ('input_tokens_details', 'cached_tokens'),
        ('prompt_cache_hit_tokens',),
        ('cached_tokens',),
    )
    reasoning = _counter(
        sources,
        ('completion_tokens_details', 'reasoning_tokens'),
        ('output_tokens_details', 'reasoning_tokens'),
    )
    missed = _counter(sources, ('prompt_cache_miss_tokens',))
    total = _counter(sources, ('total_tokens',))
    input_tokens, output_tokens = prompt.value, completion.value
    if cached.invalid or missed.invalid:
        input_tokens = None
    if input_tokens is not None and (
        (cached.value is not None and cached.value > input_tokens)
        or (
            missed.value is not None
            and (
                missed.value > input_tokens
                or (cached.value is not None and cached.value + missed.value != input_tokens)
            )
        )
    ):
        input_tokens = None
    if total.invalid or (
        total.value is not None
        and input_tokens is not None
        and output_tokens is not None
        and total.value != input_tokens + output_tokens
    ):
        input_tokens = output_tokens = None
    return UsageCounters(input_tokens, output_tokens, cached.value, reasoning.value)


class UsageCapture:
    def __init__(self) -> None:
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.cache_tokens: int | None = None
        self.reasoning_tokens: int | None = None
        self.observed = False

    def observe_raw(self, usage: Mapping[str, object]) -> None:
        counters = normalize_usage(usage)
        self.input_tokens, self.output_tokens = counters.input_tokens, counters.output_tokens
        self.cache_tokens, self.reasoning_tokens = counters.cache_tokens, counters.reasoning_tokens
        self.observed = True


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
            choices = chunk.get('choices')
            final = not choices or (
                isinstance(choices, list)
                and any(isinstance(choice, dict) and choice.get('finish_reason') is not None for choice in choices)
            )
            if usage is not None and final:
                self._usage.observe_raw(usage if isinstance(usage, dict) else {})
            # LC synthesizes zeros for absent OpenAI fields and can reject raw
            # counter types before we retain them. Text conversion needs no usage.
            yield {key: value for key, value in chunk.items() if key != 'usage'}


class CapturingCompletions:
    def __init__(self, source: AsyncOpenAI, usage: UsageCapture) -> None:
        self._source, self._usage = source, usage

    async def create(self, **kwargs: object) -> CapturingStream:
        if kwargs.get('stream') is not True:
            raise ProviderError(reason='invalid_request')
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
