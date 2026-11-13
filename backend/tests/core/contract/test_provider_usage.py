"""Real HTTP/SDK streams retain provider facts without synthetic zero counters."""

import json

import pytest

from tests.core.provider import ProviderHandler, ProviderServer
from zhiheng.modules.models.adapters.openai_chat import OpenAIChatProvider
from zhiheng.modules.models.adapters.structured import StructuredProvider
from zhiheng.modules.models.contracts import ModelMessage, ProviderError


def chat_chunk(text: str = '', *, finish: str | None = None) -> dict[str, object]:
    return {
        'id': 'usage-fixture',
        'object': 'chat.completion.chunk',
        'created': 1,
        'model': 'local',
        'choices': [{'index': 0, 'delta': {'content': text}, 'finish_reason': finish}],
    }


def serve_chat(monkeypatch: pytest.MonkeyPatch, chunks: list[dict[str, object]]) -> None:
    def response(self: ProviderHandler) -> None:
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.flush()
        self.wfile.write(b'data: [DONE]\n\n')

    monkeypatch.setattr(ProviderHandler, 'do_POST', response)


def chat_provider(provider: ProviderServer) -> OpenAIChatProvider:
    return OpenAIChatProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='local-test-key',
        model='local',
        max_output_tokens=None,
        timeout_seconds=2,
    )


@pytest.mark.parametrize('placement', ['finish-chunk', 'statistics-only'])
@pytest.mark.parametrize(
    'usage',
    [
        {
            'prompt_tokens': 8,
            'completion_tokens': 2,
            'total_tokens': 10,
            'prompt_tokens_details': {'cached_tokens': 4},
            'completion_tokens_details': {'reasoning_tokens': 1},
        },
        {
            'prompt_tokens': 8,
            'completion_tokens': 2,
            'total_tokens': 10,
            'prompt_cache_hit_tokens': 4,
            'prompt_cache_miss_tokens': 4,
            'completion_tokens_details': {'reasoning_tokens': 1},
        },
        {
            'prompt_tokens': 8,
            'completion_tokens': 2,
            'cached_tokens': 4,
            'prompt_tokens_details': {'cached_tokens': 4, 'cache_write_tokens': 2},
            'completion_tokens_details': {'reasoning_tokens': 1},
        },
        {
            'input_tokens': 8,
            'output_tokens': 2,
            'total_tokens': 10,
            'input_tokens_details': {'cached_tokens': 4},
            'output_tokens_details': {'reasoning_tokens': 1},
        },
    ],
    ids=['standard-domestic', 'deepseek-legacy-cache', 'kimi-cache-alias', 'custom-compatible-relay'],
)
async def test_documented_provider_usage_survives_real_sdk_conversion(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch, usage: dict[str, object], placement: str
) -> None:
    chunks = [chat_chunk('A'), chat_chunk('B', finish='stop')]
    if placement == 'finish-chunk':
        chunks[-1]['usage'] = usage
    else:
        chunks.append({**chat_chunk(), 'choices': [], 'usage': usage})
    serve_chat(monkeypatch, chunks)
    result = [item async for item in chat_provider(provider).stream((ModelMessage('user', 'hello'),))]
    assert ''.join(item.text for item in result) == 'AB'
    assert (result[-1].input_tokens, result[-1].output_tokens) == (8, 2)
    assert (result[-1].cache_tokens, result[-1].reasoning_tokens) == (4, 1)
    assert len([item for item in result if item.input_tokens is not None]) == 1
    assert len(provider.requests) == 1
    assert provider.requests[0]['stream_options'] == {'include_usage': True}


async def test_cumulative_and_repeated_statistics_are_not_added(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    final = {**chat_chunk(), 'choices': [], 'usage': {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}}
    serve_chat(
        monkeypatch,
        [
            {**chat_chunk('A'), 'usage': {'prompt_tokens': 8, 'completion_tokens': 1, 'total_tokens': 9}},
            {
                **chat_chunk('B', finish='stop'),
                'usage': {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10},
            },
            final,
            final,
        ],
    )
    result = [item async for item in chat_provider(provider).stream((ModelMessage('user', 'hello'),))]
    assert [(item.input_tokens, item.output_tokens) for item in result if item.input_tokens is not None] == [(8, 4)]


@pytest.mark.parametrize('usage', [None, {'prompt_tokens': 8, 'completion_tokens': 1, 'total_tokens': 9}])
async def test_missing_final_usage_does_not_promote_interim_statistics(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch, usage: dict[str, object] | None
) -> None:
    serve_chat(monkeypatch, [{**chat_chunk('A'), 'usage': usage}, chat_chunk('B', finish='stop')])
    result = [item async for item in chat_provider(provider).stream((ModelMessage('user', 'hello'),))]
    assert ''.join(item.text for item in result) == 'AB'
    assert all(item.input_tokens is item.output_tokens is None for item in result)


@pytest.mark.parametrize('counter', [False, '8', -1, 8.0, None])
async def test_invalid_final_counter_keeps_text_but_does_not_reuse_an_earlier_valid_snapshot(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch, counter: object
) -> None:
    serve_chat(
        monkeypatch,
        [
            {**chat_chunk('AB', finish='stop'), 'usage': {'prompt_tokens': 8, 'completion_tokens': 2}},
            {**chat_chunk(), 'choices': [], 'usage': {'prompt_tokens': counter, 'completion_tokens': 2}},
        ],
    )
    result = [item async for item in chat_provider(provider).stream((ModelMessage('user', 'hello'),))]
    assert ''.join(item.text for item in result) == 'AB'
    assert all(item.input_tokens is None for item in result)
    assert result[-1].output_tokens == 2


async def test_interrupted_cumulative_stream_does_not_publish_a_final_charge(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve_chat(monkeypatch, [{**chat_chunk('A'), 'usage': {'prompt_tokens': 8, 'completion_tokens': 1}}])
    result = []
    with pytest.raises(ProviderError) as caught:
        async for item in chat_provider(provider).stream((ModelMessage('user', 'hello'),)):
            result.append(item)  # noqa: PERF401 - preserve deltas yielded before the stream raises
    assert caught.value.reason == 'incomplete_stream'
    assert ''.join(item.text for item in result) == 'A'
    assert all(item.input_tokens is item.output_tokens is None for item in result)


@pytest.mark.parametrize('missing_batch', [None, 1, 2])
async def test_embedding_batches_add_confirmed_usage_and_preserve_missing_counters(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch, missing_batch: int | None
) -> None:
    def response(self: ProviderHandler) -> None:
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.requests.append(body)
        count = len(body['input'])
        result = {
            'data': [{'index': i, 'embedding': [0.1, 0.2]} for i in range(count)],
            'usage': {'input_tokens': count * 2, 'total_tokens': count * 2, 'cached_tokens': count},
        }
        if len(self.server.requests) == missing_batch:
            result.pop('usage')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    monkeypatch.setattr(ProviderHandler, 'do_POST', response)
    model = StructuredProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='local-test-key',
        model='local-vector',
        timeout_seconds=2,
        model_type='embedding',
        options={},
    )
    result = [item async for item in model.stream(tuple(ModelMessage('user', 'text') for _ in range(257)))]
    assert len(provider.requests) == 2
    expected = (514, 0, 257) if missing_batch is None else (None, None, None)
    assert (result[0].input_tokens, result[0].output_tokens, result[0].cache_tokens) == expected


@pytest.mark.parametrize('meta_input', [6, 7])
async def test_rerank_usage_and_meta_counters_must_agree(
    provider: ProviderServer, monkeypatch: pytest.MonkeyPatch, meta_input: int
) -> None:
    def response(self: ProviderHandler) -> None:
        self.server.requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    'results': [{'index': 0, 'relevance_score': 0.9}],
                    'usage': {'prompt_tokens': 6, 'completion_tokens': 0},
                    'meta': {
                        'tokens': {'input_tokens': meta_input, 'output_tokens': 0},
                        'billed_units': {'search_units': 1},
                    },
                }
            ).encode()
        )

    monkeypatch.setattr(ProviderHandler, 'do_POST', response)
    model = StructuredProvider(
        base_url=f'http://127.0.0.1:{provider.server_port}/v1',
        api_key='local-test-key',
        model='local-rerank',
        timeout_seconds=2,
        model_type='rerank',
        options={},
    )
    result = [item async for item in model.stream((ModelMessage('user', 'query'), ModelMessage('user', 'document')))]
    assert result[0].input_tokens == (6 if meta_input == 6 else None)
    assert result[0].output_tokens == 0
    assert result[0].search_units == 1
