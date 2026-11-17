"""Workspace tool wire stays on the isolated native provider and raw usage path."""

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx2
import pytest

from tests.core.unit.test_provider_diagnostics import mock_transport, provider
from zhiheng.modules.models.adapters import agent_tools
from zhiheng.modules.models.adapters.usage import UsageCapture
from zhiheng.modules.models.contracts import AgentModelMessage, ModelToolCall, ProviderError

TOOLS: tuple[dict[str, object], ...] = (
    {
        'type': 'function',
        'function': {
            'name': 'read_file',
            'description': 'Read task file',
            'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}},
        },
    },
)


def frame(delta: object = None, finish: object = None, usage: object = None) -> dict[str, object]:
    return {
        'choices': [{'index': 0, 'delta': {} if delta is None else delta, 'finish_reason': finish}],
        **({'usage': usage} if usage is not None else {}),
    }


def call(
    index: object = 0, *, identity: object = 'call-1', name: object = 'read_file', arguments: object = '{}'
) -> dict[str, object]:
    return {'index': index, 'id': identity, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


def transport(monkeypatch: pytest.MonkeyPatch, frames: list[dict[str, object]]) -> list[httpx2.Request]:
    body = ''.join('data: ' + json.dumps(value) + '\n\n' for value in frames) + 'data: [DONE]\n\n'
    return mock_transport(
        monkeypatch, lambda _: httpx2.Response(200, headers={'Content-Type': 'text/event-stream'}, content=body)
    )


async def test_fragmented_tool_calls_reasoning_and_raw_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    transport(
        monkeypatch,
        [
            frame(
                {
                    'reasoning_content': '先读取资料',
                    'tool_calls': [call(identity='call-', name='read_', arguments='{"pa')],
                }
            ),
            frame({'tool_calls': [call(identity='1', name='file', arguments='th":"inputs/a.txt"}')]}),
            frame(finish='tool_calls'),
            {'choices': [], 'usage': {'prompt_tokens': 12, 'completion_tokens': 5, 'total_tokens': 17}},
        ],
    )
    values = [
        value async for value in provider().stream_agent((AgentModelMessage(role='user', content='read'),), TOOLS)
    ]
    assert ''.join(value.reasoning for value in values) == '先读取资料'
    complete = next(value for value in values if value.result is not None)
    assert complete.result == {
        'tool_calls': [{'id': 'call-1', 'name': 'read_file', 'arguments': '{"path":"inputs/a.txt"}'}]
    }
    assert complete.finish_reason == 'stop'
    assert (values[-1].input_tokens, values[-1].output_tokens) == (12, 5)


async def test_request_preserves_assistant_tool_calls_tool_ids_and_deepseek_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = transport(
        monkeypatch, [frame({'content': 'done'}, 'stop', {'prompt_tokens': 8, 'completion_tokens': 2})]
    )
    messages = (
        AgentModelMessage(role='system', content='policy'),
        AgentModelMessage(role='user', content='request'),
        AgentModelMessage(
            role='assistant',
            content='',
            reasoning_content='已确认读取方案',
            tool_calls=(ModelToolCall(id='call-1', name='read_file', arguments='{"path":"a.txt"}'),),
        ),
        AgentModelMessage(role='tool', content='{"success":true}', tool_call_id='call-1'),
    )
    _ = [value async for value in provider().stream_agent(messages, TOOLS)]
    assert len(requests) == 1
    request = json.loads(requests[0].content)
    assert requests[0].url.path == '/v1/chat/completions'
    assert request['tools'] == list(TOOLS) and request['stream_options'] == {'include_usage': True}
    assert request['max_completion_tokens'] == 32
    assert request['messages'][2] == {
        'role': 'assistant',
        'content': '',
        'reasoning_content': '已确认读取方案',
        'tool_calls': [
            {'id': 'call-1', 'type': 'function', 'function': {'name': 'read_file', 'arguments': '{"path":"a.txt"}'}}
        ],
    }
    assert request['messages'][3] == {'role': 'tool', 'content': '{"success":true}', 'tool_call_id': 'call-1'}


@pytest.mark.parametrize(
    'usage,expected',
    [
        (None, (None, None)),
        ({'prompt_tokens': True, 'completion_tokens': '0'}, (None, None)),
        ({'prompt_tokens': 8}, (8, None)),
        ({'prompt_tokens': 0, 'completion_tokens': 0}, (0, 0)),
    ],
    ids=['missing', 'invalid-raw-types', 'partial', 'confirmed-zero'],
)
async def test_missing_or_invalid_usage_is_never_synthesized_as_zero(
    monkeypatch: pytest.MonkeyPatch, usage: object, expected: tuple[int | None, int | None]
) -> None:
    transport(monkeypatch, [frame({'content': 'done'}, 'stop', usage)])
    values = [value async for value in provider().stream_agent((AgentModelMessage(role='user', content='test'),), ())]
    assert (values[-1].input_tokens, values[-1].output_tokens) == expected
    assert any(value.finish_reason == 'stop' for value in values)


@pytest.mark.parametrize(
    'wire,reason',
    [
        ([frame({'tool_calls': [call(arguments='{broken')]}, 'tool_calls')], 'invalid_response'),
        ([frame({'tool_calls': [call(arguments='[]')]}, 'tool_calls')], 'invalid_response'),
        ([frame({'tool_calls': [call(name='not_allowed')]}, 'tool_calls')], 'invalid_response'),
        ([frame({'tool_calls': [call(identity='')]}, 'tool_calls')], 'invalid_response'),
        ([frame({'tool_calls': [call(), call(1)]}, 'tool_calls')], 'invalid_response'),
        ([frame({'tool_calls': [call(True)]}, 'tool_calls')], 'invalid_response'),
        ([frame({'tool_calls': [call(20)]}, 'tool_calls')], 'invalid_response'),
        ([frame({'tool_calls': [call(arguments=1)]}, 'tool_calls')], 'invalid_response'),
        ([frame({'content': ['not text']}, 'stop')], 'invalid_content'),
        ([frame({'reasoning_content': 7}, 'stop')], 'invalid_reasoning'),
        ([frame(finish='tool_calls')], 'invalid_response'),
        ([frame({'tool_calls': [call()]}, 'length')], 'invalid_response'),
        ([frame({'content': 'partial'})], 'incomplete_stream'),
        ([frame(finish='content_filter')], 'unsupported_finish_reason'),
        ([frame(finish={})], 'unsupported_finish_reason'),
    ],
    ids=[
        'malformed-json',
        'non-object-args',
        'unregistered-tool',
        'missing-id',
        'duplicate-id',
        'boolean-index',
        'index-overflow',
        'non-string-args',
        'invalid-content',
        'invalid-reasoning',
        'empty-tool-finish',
        'truncated-tool-call',
        'missing-finish',
        'unsupported-finish',
        'non-string-finish',
    ],
)
async def test_invalid_tool_protocol_never_produces_executable_calls(
    monkeypatch: pytest.MonkeyPatch, wire: list[dict[str, object]], reason: str
) -> None:
    transport(monkeypatch, wire)
    with pytest.raises(ProviderError) as caught:
        _ = [value async for value in provider().stream_agent((AgentModelMessage(role='user', content='test'),), TOOLS)]
    assert caught.value.reason == reason
    assert caught.value.message == '模型调用失败，请稍后重试。'


async def test_parallel_call_order_uses_wire_index(monkeypatch: pytest.MonkeyPatch) -> None:
    transport(
        monkeypatch, [frame({'tool_calls': [call(1, identity='second'), call(0, identity='first')]}, 'tool_calls')]
    )
    values = [
        value async for value in provider().stream_agent((AgentModelMessage(role='user', content='test'),), TOOLS)
    ]
    assert values[-1].result is not None
    assert [item['id'] for item in values[-1].result['tool_calls']] == ['first', 'second']


async def test_usage_is_preserved_before_a_protocol_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    transport(
        monkeypatch,
        [frame({'tool_calls': [call(name='not_allowed')]}, 'tool_calls', {'prompt_tokens': 9, 'completion_tokens': 3})],
    )
    stream = provider().stream_agent((AgentModelMessage(role='user', content='test'),), TOOLS)
    usage = await anext(stream)
    assert (usage.input_tokens, usage.output_tokens) == (9, 3)
    with pytest.raises(ProviderError):
        await anext(stream)


async def test_finished_response_with_observed_usage_does_not_wait_for_keepalive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = UsageCapture()
    closed = []

    class Source:
        async def __aenter__(self) -> 'Source':
            return self

        async def __aexit__(self, *_args: object) -> None:
            closed.append(True)

        async def __aiter__(self) -> AsyncIterator[dict[str, object]]:
            captured.observe_raw({'prompt_tokens': 4, 'completion_tokens': 1})
            yield frame({'content': 'done'}, 'stop')
            raise AssertionError('A complete response must not await upstream keepalive')

    async def create(**_kwargs: object) -> Source:
        return Source()

    monkeypatch.setattr(agent_tools, 'CapturingCompletions', lambda *_args: SimpleNamespace(create=create))
    values = [
        value
        async for value in agent_tools.stream_agent(
            None,
            captured,
            (AgentModelMessage(role='user', content='test'),),
            (),
            model='test',
            max_output_tokens=32,
            options={},
        )
    ]
    assert values[-1].input_tokens == 4 and values[-1].output_tokens == 1 and closed == [True]
