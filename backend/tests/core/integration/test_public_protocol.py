import asyncio
import json
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from tests.core.integration.test_developer import provision
from zhiheng.modules.billing.orm import Invocation
from zhiheng.worker.execution import execute_run


async def drive(container: object, task: asyncio.Task[httpx.Response]) -> httpx.Response:
    seen: set[UUID] = set()
    for _ in range(200):
        if task.done():
            return await task
        async with container.database.sessions() as session:
            rows = list(await session.scalars(select(Invocation)))
        for row in rows:
            if row.run_id not in seen:
                seen.add(row.run_id)
                await execute_run(container, row.run_id)
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    raise AssertionError('Public response did not complete')


@pytest.mark.asyncio
@pytest.mark.parametrize('upstream,successful', [('local-text', True), ('truncated', False)])
async def test_chat_delta_sse_and_early_eof(
    admin: httpx.AsyncClient, model_id: str, app: object, upstream: str, successful: bool
) -> None:
    await admin.patch(f'/api/models/{model_id}', json={'upstream_model': upstream})
    app_id, key = await provision(admin, model_id)
    task = asyncio.create_task(
        admin.post(
            '/v1/chat/completions',
            headers={'Authorization': f'Bearer {key["api_key"]}'},
            json={
                'model': model_id,
                'messages': [{'role': 'developer', 'content': 'Be helpful'}, {'role': 'user', 'content': 'Hi'}],
                'temperature': 0.4,
                'stream': True,
            },
        )
    )
    result = await drive(app.state.container, task)
    assert result.status_code == 200
    chunks = [line[6:] for line in result.text.splitlines() if line.startswith('data: ')]
    assert json.loads(chunks[0])['choices'][0]['delta'] == {'role': 'assistant'}
    if successful:
        assert chunks[-1] == '[DONE]'
        assert json.loads(chunks[-2])['choices'][0]['finish_reason'] == 'stop'
        assert ''.join(json.loads(line)['choices'][0]['delta'].get('content', '') for line in chunks[:-1]) == '你好'
    else:
        assert '[DONE]' not in chunks
        assert json.loads(chunks[-1])['error']['code'] == 'provider_unavailable'
    ledger = (await admin.get(f'/api/developer/applications/{app_id}/usage')).json()['items'][0]
    assert ledger['run_status'] == ('completed' if successful else 'failed')
    assert ledger['status'] == ('known' if successful else 'unknown')


@pytest.mark.asyncio
async def test_embedding_and_rerank_use_worker_and_actual_usage(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    connection_id = (await admin.get('/api/models')).json()[0]['connection_id']
    from uuid import uuid4

    application = (
        await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'Vectors'})
    ).json()
    for kind, endpoint, payload in [
        ('embedding', 'embeddings', {'input': ['first', 'second'], 'dimensions': 2}),
        (
            'rerank',
            'rerank',
            {'query': 'first', 'documents': ['first', {'text': 'second'}], 'top_n': 1, 'return_documents': True},
        ),
    ]:
        model = (
            await admin.post(
                '/api/models',
                json={'name': kind, 'connection_id': connection_id, 'upstream_model': kind, 'model_type': kind},
            )
        ).json()
        price = (
            {'billing_mode': 'token', 'input_price': '0.01'}
            if kind == 'embedding'
            else {'billing_mode': 'search_unit', 'search_price': '0.25'}
        )
        rate = await admin.post(f'/api/models/{model["id"]}/rate-cards', json=price)
        assert rate.status_code == 201, rate.text
        key = (
            await admin.post(
                f'/api/developer/applications/{application["id"]}/keys', json={'name': kind, 'model_ids': [model['id']]}
            )
        ).json()
        task = asyncio.create_task(
            admin.post(
                '/v1/' + endpoint,
                headers={'Authorization': f'Bearer {key["api_key"]}'},
                json={'model': model['id'], **payload},
            )
        )
        response = await drive(app.state.container, task)
        assert response.status_code == 200, response.text
        assert response.json()['model'] == model['id']
        if kind == 'embedding':
            assert response.json()['data'][1]['embedding'] == [0.3, 0.4]
            assert response.json()['usage']['prompt_tokens'] == 6
        else:
            assert response.json()['results'] == [{'index': 0, 'relevance_score': 0.9, 'document': {'text': 'first'}}]
            assert response.json()['meta']['billed_units']['search_units'] == 2
            assert response.json()['meta']['tokens'] == {'input_tokens': 6, 'output_tokens': 0}
        usage = (
            await admin.get(f'/api/developer/applications/{application["id"]}/usage', params={'operation': kind})
        ).json()['items'][0]
        assert usage['billable_cost'] is None
        assert usage['status'] == 'known'
    assert provider.requests[-2]['input'] == ['first', 'second']
    assert provider.requests[-2]['dimensions'] == 2
    assert provider.request_headers[-1]['authorization'] == 'Bearer local-test-key'


@pytest.mark.asyncio
async def test_disconnect_and_header_failure_cancel_pending_reservation(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from tests.core.integration.test_developer import admitted
    from zhiheng.api.inference.streaming import LifecycleStream, cancel, snapshots
    from zhiheng.modules.billing.orm import UsageAccount

    _, key = await provision(admin, model_id)
    container = app.state.container
    run, identity = await admitted(container, key['api_key'], model_id)

    class Disconnected:
        async def is_disconnected(self) -> bool:
            return True

    source = snapshots(container, Disconnected(), run.id, identity.owner_id, key['api_key'], scope='models:invoke')
    assert (await anext(source)).status == 'pending'
    with pytest.raises(asyncio.CancelledError):
        await anext(source)
    async with container.database.sessions() as session:
        snapshot = await container.runs.snapshot(session, run.id, identity.owner_id)
        account = await session.get(UsageAccount, identity.owner_id)
        assert snapshot.status == 'cancelled' and account.reserved_tokens == 0
    next_run, _ = await admitted(container, key['api_key'], model_id)

    async def frames() -> object:
        yield 'data: hello\n\n'

    response = LifecycleStream(frames(), lambda: cancel(container, next_run.id, identity.owner_id))

    async def receive() -> dict[str, object]:
        return {'type': 'http.disconnect'}

    async def fail(_message: object) -> None:
        raise RuntimeError('send start failed')

    with pytest.raises(RuntimeError, match='send start failed'):
        await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, fail)
    async with container.database.sessions() as session:
        assert (await container.runs.snapshot(session, next_run.id, identity.owner_id)).status == 'cancelled'
        assert (await session.get(UsageAccount, identity.owner_id)).reserved_tokens == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['embedding', 'rerank'])
async def test_connection_smoke_uses_declared_model_type(
    admin: httpx.AsyncClient, model_id: str, app: object, kind: str
) -> None:
    from uuid import uuid4

    connection = (await admin.get('/api/models')).json()[0]['connection_id']
    model = (
        await admin.post(
            '/api/models', json={'name': kind, 'connection_id': connection, 'upstream_model': kind, 'model_type': kind}
        )
    ).json()
    response = await admin.post(f'/api/models/{model["id"]}/test', json={'client_request_id': str(uuid4())})
    assert response.status_code == 202, response.text
    await execute_run(app.state.container, UUID(response.json()['id']))
    snapshot = (await admin.get('/api/runs/' + response.json()['id'])).json()
    assert snapshot['status'] == 'completed', snapshot
    assert ('data' if kind == 'embedding' else 'results') in snapshot['result']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['embedding', 'rerank'])
async def test_unbilled_structured_missing_usage_stays_unknown_without_reservation_or_price(
    admin: httpx.AsyncClient, model_id: str, app: object, kind: str
) -> None:
    from uuid import uuid4

    from zhiheng.modules.billing.orm import UsageAccount

    connection = (await admin.get('/api/models')).json()[0]['connection_id']
    model = (
        await admin.post(
            '/api/models',
            json={'name': kind, 'connection_id': connection, 'upstream_model': kind + '-no-usage', 'model_type': kind},
        )
    ).json()
    price = (
        {'billing_mode': 'search_unit', 'search_price': '0.25'}
        if kind == 'rerank'
        else {'billing_mode': 'token', 'input_price': '0.01'}
    )
    await admin.post(f'/api/models/{model["id"]}/rate-cards', json=price)
    application = (
        await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'Unknown'})
    ).json()
    key = (
        await admin.post(
            f'/api/developer/applications/{application["id"]}/keys', json={'name': 'SDK', 'model_ids': [model['id']]}
        )
    ).json()
    payload = (
        {'input': ['first', 'second']} if kind == 'embedding' else {'query': 'first', 'documents': ['first', 'second']}
    )
    task = asyncio.create_task(
        admin.post(
            '/v1/' + ('embeddings' if kind == 'embedding' else 'rerank'),
            headers={'Authorization': f'Bearer {key["api_key"]}'},
            json={'model': model['id'], **payload},
        )
    )
    response = await drive(app.state.container, task)
    assert response.status_code == 200, response.text
    usage = (await admin.get(f'/api/developer/applications/{application["id"]}/usage')).json()['items'][0]
    assert usage['status'] == 'unknown'
    assert usage['billable_cost'] is None
    assert usage['input_tokens'] is None and usage['output_tokens'] is None
    async with app.state.container.database.sessions() as session:
        account = await session.get(UsageAccount, UUID(application['owner_id']))
        assert account.reserved_tokens == account.used_tokens == 0


@pytest.mark.asyncio
async def test_public_unknown_routes_and_malformed_transport_use_openai_errors(admin: httpx.AsyncClient) -> None:
    missing = await admin.get('/v1/not-found')
    assert missing.status_code == 404
    assert set(missing.json()['error']) == {'message', 'type', 'param', 'code'}
    for headers, body in [
        ([('Content-Type', 'application/json'), ('Content-Type', 'application/json')], b'{}'),
        ([('Content-Type', 'application/json'), ('Content-Length', '9')], b'{}'),
    ]:
        response = await admin.post('/v1/chat/completions', headers=headers, content=body)
        assert response.status_code == 400
        assert response.json()['error']['code'] == 'invalid_request'


@pytest.mark.asyncio
async def test_public_openapi_describes_actual_payload_and_bearer(client: httpx.AsyncClient) -> None:
    document = (await client.get('/openapi.json')).json()
    operation = document['paths']['/v1/chat/completions']['post']
    assert operation['requestBody']['content']['application/json']['schema']['$ref'].endswith('/ChatBody')
    assert operation['security'] == [{'HTTPBearer': []}]
