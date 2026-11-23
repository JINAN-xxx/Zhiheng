"""Tool-call streaming over the same isolated SDK transport and raw usage capture."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from openai import APIError, AsyncOpenAI

from zhiheng.modules.models.adapters.errors import provider_error
from zhiheng.modules.models.adapters.usage import CapturingCompletions, UsageCapture
from zhiheng.modules.models.contracts import AgentModelMessage, ModelDelta, ModelToolCall, ProviderError

MAX_TOOL_CALLS = 20
MAX_TOOL_ARGUMENTS = 100000
MAX_TOOL_ID = 200
MAX_TOOL_NAME = 100


@dataclass
class _ToolFragments:
    id: str = ''
    name: str = ''
    arguments: str = ''

    def append(self, value: dict[str, object]) -> bool:
        previous = (self.id, self.name, len(self.arguments))
        identity = value.get('id')
        if identity is not None:
            if not isinstance(identity, str):
                raise ProviderError(reason='invalid_response')
            if identity != self.id:
                self.id += identity
        function = value.get('function', {})
        if not isinstance(function, dict):
            raise ProviderError(reason='invalid_response')
        self._function(function)
        if len(self.arguments) > MAX_TOOL_ARGUMENTS or len(self.id) > MAX_TOOL_ID or len(self.name) > MAX_TOOL_NAME:
            raise ProviderError(reason='projection_limit')
        return previous != (self.id, self.name, len(self.arguments))

    def _function(self, function: dict[str, object]) -> None:
        name, arguments = function.get('name'), function.get('arguments')
        if name is not None:
            if not isinstance(name, str):
                raise ProviderError(reason='invalid_response')
            if name != self.name:
                self.name += name
        if arguments is not None:
            if not isinstance(arguments, str):
                raise ProviderError(reason='invalid_response')
            self.arguments += arguments


def _calls(delta: dict[str, object], fragments: dict[int, _ToolFragments]) -> bool:
    values = delta.get('tool_calls')
    if values is None:
        return False
    if not isinstance(values, list):
        raise ProviderError(reason='invalid_response')
    changed = False
    for value in values:
        if not isinstance(value, dict):
            raise ProviderError(reason='invalid_response')
        index = value.get('index')
        if type(index) is not int or not 0 <= index < MAX_TOOL_CALLS:
            raise ProviderError(reason='invalid_response')
        changed = fragments.setdefault(index, _ToolFragments()).append(value) or changed
    return changed


def _wire(message: AgentModelMessage) -> dict[str, object]:
    result: dict[str, object] = {'role': message.role, 'content': message.content}
    if message.tool_call_id:
        result['tool_call_id'] = message.tool_call_id
    if message.tool_calls:
        result['tool_calls'] = [
            {'id': call.id, 'type': 'function', 'function': {'name': call.name, 'arguments': call.arguments}}
            for call in message.tool_calls
        ]
    if message.reasoning_content:
        result['reasoning_content'] = message.reasoning_content
    return result


def _choice(raw: dict[str, object]) -> dict[str, object] | None:
    choices = raw.get('choices')
    if choices is None or choices == []:
        return None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ProviderError(reason='invalid_response')
    return choices[0]


def _content(delta: dict[str, object], name: str) -> str:
    value = delta.get(name)
    if value is None:
        return ''
    if not isinstance(value, str):
        raise ProviderError(reason='invalid_content' if name == 'content' else 'invalid_reasoning')
    return value


def _finish(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in {'stop', 'length', 'tool_calls'}:
        raise ProviderError(reason='unsupported_finish_reason')
    return value


@dataclass
class _Response:
    fragments: dict[int, _ToolFragments] = field(default_factory=dict)
    finish: str | None = None

    def observe(self, raw: dict[str, object]) -> ModelDelta | None:
        choice = _choice(raw)
        if choice is None:
            return None
        value = choice.get('delta')
        delta = {} if value is None else value
        if not isinstance(delta, dict):
            raise ProviderError(reason='invalid_response')
        text, reasoning = _content(delta, 'content'), _content(delta, 'reasoning_content')
        finish = _finish(choice.get('finish_reason'))
        if self.finish is not None and (
            text or reasoning or delta.get('tool_calls') or finish not in {None, self.finish}
        ):
            raise ProviderError(reason='invalid_response')
        activity = _calls(delta, self.fragments)
        activity = activity or (finish is not None and self.finish is None)
        self.finish = finish or self.finish
        return ModelDelta(text, reasoning=reasoning, activity=activity) if text or reasoning or activity else None

    def result(self, tools: tuple[dict[str, object], ...]) -> ModelDelta:
        if self.finish is None:
            raise ProviderError(reason='incomplete_stream')
        names = {
            function['name']
            for tool in tools
            if isinstance((function := tool.get('function')), dict) and isinstance(function.get('name'), str)
        }
        calls = [self._call(self.fragments[index], names) for index in sorted(self.fragments)]
        if len({call.id for call in calls}) != len(calls) or (self.finish == 'tool_calls' and not calls):
            raise ProviderError(reason='invalid_response')
        return ModelDelta(
            finish_reason='length' if self.finish == 'length' else 'stop',
            result={'tool_calls': [call.model_dump(mode='json') for call in calls]},
        )

    def _call(self, fragment: _ToolFragments, names: set[object]) -> ModelToolCall:
        arguments = fragment.arguments or '{}'
        if not isinstance(json.loads(arguments), dict) or self.finish == 'length':
            raise ProviderError(reason='invalid_response')
        call = ModelToolCall(id=fragment.id, name=fragment.name, arguments=arguments)
        if call.name not in names:
            raise ProviderError(reason='tool_unavailable')
        return call


def _request(
    messages: tuple[AgentModelMessage, ...],
    tools: tuple[dict[str, object], ...],
    model: str,
    max_output_tokens: int | None,
    options: dict[str, object],
) -> dict[str, object]:
    request: dict[str, object] = {
        **options,
        'model': model,
        'messages': [_wire(message) for message in messages],
        'stream': True,
        'stream_options': {'include_usage': True},
    }
    if max_output_tokens is not None:
        request['max_completion_tokens'] = max_output_tokens
    if tools:
        request['tools'] = list(tools)
    return request


async def stream_agent(
    client: AsyncOpenAI,
    captured: UsageCapture,
    messages: tuple[AgentModelMessage, ...],
    tools: tuple[dict[str, object], ...],
    *,
    model: str,
    max_output_tokens: int | None,
    options: dict[str, object],
) -> AsyncIterator[ModelDelta]:
    state = _Response()
    terminal_emitted = False
    failure: ProviderError | None = None
    try:
        source = await CapturingCompletions(client, captured).create(
            **_request(messages, tools, model, max_output_tokens, options)
        )
        async with source:
            async for raw in source:
                delta = state.observe(raw)
                if delta is not None:
                    yield delta
                if state.finish is not None and not terminal_emitted:
                    # Expose a validated terminal result while awaiting trailing usage.
                    # Native Runs remain running until the stream has been consumed.
                    try:
                        terminal = state.result(tools)
                    except ProviderError as error:
                        if error.reason != 'tool_unavailable':
                            raise
                        # The tool cannot run, but the provider may still send its usage.
                        failure = error
                    else:
                        yield terminal
                    terminal_emitted = True
                # A terminal choice and the raw final usage are complete facts. Some compatible
                # providers keep the transport alive afterwards; no further body is needed.
                if state.finish is not None and captured.observed:
                    break
        if not terminal_emitted:
            yield state.result(tools)
    except ProviderError as error:
        failure = failure or error
    except (APIError, TimeoutError, ValueError) as error:
        failure = failure or provider_error(error)
    if captured.observed:
        yield ModelDelta(
            input_tokens=captured.input_tokens,
            output_tokens=captured.output_tokens,
            cache_tokens=captured.cache_tokens,
            reasoning_tokens=captured.reasoning_tokens,
        )
    if failure is not None:
        raise failure from None
