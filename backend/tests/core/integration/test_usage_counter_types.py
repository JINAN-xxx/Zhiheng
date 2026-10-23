"""Provider counters retain raw JSON types before any SDK/typed-schema coercion."""

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from tests.core.integration.test_public_protocol import drive
from tests.core.provider import ProviderHandler
from zhiheng.modules.developer.orm import Invocation, KeyMinuteBucket, UsageAccount


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['embedding', 'rerank', 'chat'])
@pytest.mark.parametrize(
    'counter',
    [False, '0', -1, 0.0, 0.5, None, 0],
    ids=['bool', 'string', 'negative', 'integer-float', 'fractional-float', 'null', 'real-zero'],
)
async def test_raw_counter_types_cannot_release_unknown_reservations(
    admin: httpx.AsyncClient, model_id: str, app: object, monkeypatch: pytest.MonkeyPatch, kind: str, counter: object
) -> None:
    def response(self: ProviderHandler) -> None:
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if kind == 'chat':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for chunk in [
                {
                    'id': 'typed-counter',
                    'object': 'chat.completion.chunk',
                    'created': 1,
                    'model': 'local',
                    'choices': [{'index': 0, 'delta': {'content': 'answer'}, 'finish_reason': 'stop'}],
                },
                {
                    'id': 'typed-counter',
                    'object': 'chat.completion.chunk',
                    'created': 1,
                    'model': 'local',
                    'choices': [],
                    'usage': {'prompt_tokens': counter, 'completion_tokens': counter, 'total_tokens': 0},
                },
            ]:
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        else:
            payload = (
                {
                    'data': [{'index': index, 'embedding': [0.1, 0.2]} for index, _ in enumerate(body['input'])],
                    'usage': {'prompt_tokens': counter},
                }
                if kind == 'embedding'
                else {
                    'results': [{'index': 0, 'relevance_score': 0.9}],
                    'meta': {'billed_units': {'search_units': counter}},
                    'usage': {'prompt_tokens': counter, 'completion_tokens': counter},
                }
            )
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

    monkeypatch.setattr(ProviderHandler, 'do_POST', response)
    connection = (await admin.get('/api/models')).json()[0]['connection_id']
    model = (
        await admin.post(
            '/api/models', json={'name': kind, 'connection_id': connection, 'upstream_model': kind, 'model_type': kind}
        )
    ).json()
    rate = (
        {'billing_mode': 'search_unit', 'search_price': '0.25'}
        if kind == 'rerank'
        else {'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.02'}
    )
    assert (await admin.post(f'/api/models/{model["id"]}/rate-cards', json=rate)).status_code == 201
    application = (
        await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'Raw counters'})
    ).json()
    key = (
        await admin.post(
            f'/api/developer/applications/{application["id"]}/keys', json={'name': 'SDK', 'model_ids': [model['id']]}
        )
    ).json()
    request = (
        {'input': ['text']}
        if kind == 'embedding'
        else {'query': 'text', 'documents': ['text']}
        if kind == 'rerank'
        else {'messages': [{'role': 'user', 'content': 'text'}]}
    )
    endpoint = {'embedding': 'embeddings', 'rerank': 'rerank', 'chat': 'chat/completions'}[kind]
    task = asyncio.create_task(
        admin.post(
            '/v1/' + endpoint,
            headers={'Authorization': f'Bearer {key["api_key"]}'},
            json={'model': model['id'], **request},
        )
    )
    result = await drive(app.state.container, task)
    assert result.status_code in {200, 502}, result.text
    async with app.state.container.database.sessions() as session:
        invocation = await session.scalar(
            select(Invocation).where(Invocation.application_id == UUID(application['id']))
        )
        account = await session.get(UsageAccount, UUID(application['owner_id']))
        bucket = await session.scalar(select(KeyMinuteBucket).where(KeyMinuteBucket.key_id == UUID(key['id'])))
        if type(counter) is int and counter == 0:
            assert result.status_code == 200, result.text
            assert invocation.status == 'known' and invocation.billable_cost == 0 and invocation.charged_tokens == 0
            assert account.reserved_tokens == 0 and bucket.tokens == 0
        else:
            assert (
                invocation.status == 'unknown'
                and invocation.billable_cost is None
                and invocation.charged_tokens is None
            )
            assert account.reserved_tokens == invocation.reserved_tokens and bucket.tokens == invocation.reserved_tokens
        assert account.used_tokens == 0
