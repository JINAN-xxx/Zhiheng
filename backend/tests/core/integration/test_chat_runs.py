import asyncio
from uuid import UUID, uuid4

import httpx

from tests.core.provider import ProviderServer


async def admit(admin: httpx.AsyncClient, model_id: str, *, client_request_id: str | None = None) -> tuple[str, dict]:
    conversation = (await admin.post('/api/chat/conversations', json={'title': 'Test chat'})).json()
    response = await admin.post(
        f'/api/chat/conversations/{conversation["id"]}/runs',
        json={
            'model_id': model_id,
            'message': '你好',
            'client_request_id': client_request_id or str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    return conversation['id'], response.json()


async def test_chat_persists_result_and_known_usage(
    admin: httpx.AsyncClient,
    model_id: str,
    app: object,
    provider: ProviderServer,
) -> None:
    from zhiheng.worker.execution import execute_run

    conversation_id, run = await admit(admin, model_id)
    await execute_run(app.state.container, UUID(run['id']))
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'completed'
    assert result['text'] == '你好'
    assert result['usage'] == {'input_tokens': 8, 'output_tokens': 2, 'status': 'known'}
    messages = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    assert [message['role'] for message in messages] == ['user', 'assistant']
    assert all(message['run_id'] == run['id'] for message in messages)
    usage = (await admin.get('/api/usage')).json()
    assert usage['used_tokens'] == 10 and usage['reserved_tokens'] == 0
    assert len(usage['records']) == 1
    assert len(provider.requests) == 1
    events = await admin.get(f'/api/runs/{run["id"]}/events')
    assert 'event: snapshot' in events.text and 'completed' in events.text


async def test_duplicate_delivery_and_request_do_not_repeat_model_call(
    admin: httpx.AsyncClient,
    model_id: str,
    app: object,
    provider: ProviderServer,
) -> None:
    from zhiheng.worker.execution import execute_run

    key = str(uuid4())
    conversation_id, run = await admit(admin, model_id, client_request_id=key)
    duplicate = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': '你好',
            'client_request_id': key,
        },
    )
    assert duplicate.status_code == 202 and duplicate.json()['id'] == run['id']
    await asyncio.gather(
        execute_run(app.state.container, UUID(run['id'])), execute_run(app.state.container, UUID(run['id']))
    )
    again = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': '你好',
            'client_request_id': key,
        },
    )
    assert again.json()['id'] == run['id']
    assert len(provider.requests) == 1
    different = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': 'different',
            'client_request_id': key,
        },
    )
    assert different.status_code == 409


async def test_other_users_cannot_read_chat_or_run(admin: httpx.AsyncClient, model_id: str, app: object) -> None:
    conversation_id, run = await admit(admin, model_id)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get(f'/api/runs/{run["id"]}')).status_code == 404
        assert (await member.get(f'/api/chat/conversations/{conversation_id}/messages')).status_code == 404
        assert (await member.post(f'/api/runs/{run["id"]}/cancel', json={})).status_code == 404


async def test_pending_cancel_releases_reservation_without_calling_provider(
    admin: httpx.AsyncClient,
    model_id: str,
    app: object,
    provider: ProviderServer,
) -> None:
    from zhiheng.worker.execution import execute_run

    _conversation_id, run = await admit(admin, model_id)
    cancelled = await admin.post(f'/api/runs/{run["id"]}/cancel', json={})
    assert cancelled.json()['status'] == 'cancelled'
    await execute_run(app.state.container, UUID(run['id']))
    usage = (await admin.get('/api/usage')).json()
    assert usage['reserved_tokens'] == 0 and usage['used_tokens'] == 0
    assert not provider.requests


async def test_unbilled_parallel_runs_are_not_blocked_by_a_lifetime_token_quota(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    app.state.container.settings.token_limit = 100
    conversations = [(await admin.post('/api/chat/conversations', json={})).json() for _ in range(2)]
    results = await asyncio.gather(
        *[
            admin.post(
                f'/api/chat/conversations/{conversation["id"]}/runs',
                json={
                    'model_id': model_id,
                    'message': 'hello',
                    'client_request_id': str(uuid4()),
                },
            )
            for conversation in conversations
        ]
    )
    assert sorted(result.status_code for result in results) == [202, 202]


async def test_unsupported_tools_are_not_silently_discarded(admin: httpx.AsyncClient, model_id: str) -> None:
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    response = await admin.post(
        f'/api/chat/conversations/{conversation["id"]}/runs',
        json={
            'model_id': model_id,
            'message': 'hello',
            'client_request_id': str(uuid4()),
            'tools': [{'name': 'unimplemented'}],
        },
    )
    assert response.status_code == 422
