"""Private assistant conversations use durable runs and the latest publication for new messages."""

import asyncio
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI

from tests.core.provider import ProviderServer
from zhiheng.worker.execution import execute_run


async def assistant(admin: httpx.AsyncClient, model_id: str, **changes: object) -> dict:
    value = await admin.post(
        '/api/agents',
        json={
            'name': '私人助手',
            'model_id': model_id,
            'system_prompt': 'PUBLISHED SYSTEM',
            'welcome_message': 'STATIC WELCOME',
            'max_tokens': 32,
            **changes,
        },
    )
    assert value.status_code == 201, value.text
    result = await admin.post('/api/agents/' + value.json()['id'] + '/publish', json={'expected_version': 1})
    assert result.status_code == 200, result.text
    return result.json()


async def conversation(admin: httpx.AsyncClient, agent_id: str) -> dict:
    response = await admin.post(f'/api/agents/{agent_id}/conversations', json={'client_request_id': str(uuid4())})
    assert response.status_code == 201, response.text
    return response.json()


async def submit(admin: httpx.AsyncClient, agent_id: str, conversation_id: str, message: str) -> dict:
    response = await admin.post(
        f'/api/agents/{agent_id}/conversations/{conversation_id}/runs',
        json={
            'message': message,
            'client_request_id': str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


async def test_published_conversation_history_is_private_and_new_turns_follow_latest_publication(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    agent = await assistant(admin, model_id)
    first = await conversation(admin, agent['id'])
    path = f'/api/agents/{agent["id"]}/conversations'
    assert first['published_version'] == 1 and first['welcome_message'] == 'STATIC WELCOME'
    assert not provider.requests  # Creating a session or reading its greeting is free.
    run = await submit(admin, agent['id'], first['id'], 'FIRST PRIVATE QUESTION')
    assert run['resource_type'] == 'agent_chat'
    await execute_run(app.state.container, UUID(run['id']))
    detail = await admin.get(path + '/' + first['id'])
    assert detail.status_code == 200, detail.text
    assert detail.json()['turns'][0]['run']['text'] == '你好'
    assert 'STATIC WELCOME' not in str(provider.requests)
    assert 'PUBLISHED SYSTEM' in str(provider.requests)
    assert (await admin.get(path)).json()[0]['title'] == 'FIRST PRIVATE QUESTION'
    await admin.patch(
        '/api/agents/' + agent['id'],
        json={
            'name': '新版助手',
            'model_id': model_id,
            'system_prompt': 'NEW SYSTEM',
            'welcome_message': 'NEW WELCOME',
            'max_tokens': 32,
            'expected_version': 2,
        },
    )
    published = await admin.post('/api/agents/' + agent['id'] + '/publish', json={'expected_version': 3})
    assert published.status_code == 200, published.text
    run = await submit(admin, agent['id'], first['id'], 'SECOND QUESTION')
    await execute_run(app.state.container, UUID(run['id']))
    wire = provider.requests[-1]['messages']
    assert wire[0]['content'] == 'NEW SYSTEM'
    assert [row['content'] for row in wire[1:]] == ['FIRST PRIVATE QUESTION', '你好', 'SECOND QUESTION']
    history = (await admin.get(path + '/' + first['id'])).json()
    assert [turn['published_version'] for turn in history['turns']] == [1, 2]
    assert [turn['agent_name'] for turn in history['turns']] == ['私人助手', '新版助手']
    assert history['conversation']['welcome_message'] == 'NEW WELCOME'
    fresh = await conversation(admin, agent['id'])
    assert fresh['published_version'] == 2 and fresh['welcome_message'] == 'NEW WELCOME'
    run = await submit(admin, agent['id'], fresh['id'], 'FRESH SESSION')
    await execute_run(app.state.container, UUID(run['id']))
    assert [row['content'] for row in provider.requests[-1]['messages']] == ['NEW SYSTEM', 'FRESH SESSION']
    other = await assistant(admin, model_id, name='另一个助手')
    assert (await admin.get(f'/api/agents/{other["id"]}/conversations/{first["id"]}')).status_code == 404
    from tests.core.integration.test_platform_module_permissions import grant

    await grant(app, 'agents')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get(path)).json() == []
        assert (await member.get(path + '/' + first['id'])).status_code == 404
        assert (await member.post(path, json={'client_request_id': str(uuid4())})).status_code == 404
        assert (await member.get('/api/runs/' + run['id'])).status_code == 404
        assert (
            await member.post(
                path + '/' + first['id'] + '/runs', json={'message': 'steal', 'client_request_id': str(uuid4())}
            )
        ).status_code == 404


async def test_concurrent_replay_busy_cancel_and_refresh_use_one_ledger(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    agent = await assistant(admin, model_id)
    path = f'/api/agents/{agent["id"]}/conversations'
    body = {'client_request_id': str(uuid4())}
    first, second = await asyncio.gather(admin.post(path, json=body), admin.post(path, json=body))
    assert first.status_code == second.status_code == 201
    assert first.json()['id'] == second.json()['id']
    run_path = path + '/' + first.json()['id'] + '/runs'
    body = {'message': 'ONLY ONCE', 'client_request_id': str(uuid4())}
    first_run, replay = await asyncio.gather(admin.post(run_path, json=body), admin.post(run_path, json=body))
    assert first_run.status_code == replay.status_code == 202, (first_run.text, replay.text)
    assert first_run.json()['id'] == replay.json()['id']
    assert (await admin.post(run_path, json={**body, 'message': 'CHANGED'})).status_code == 409
    busy = await admin.post(run_path, json={'message': 'another', 'client_request_id': str(uuid4())})
    assert busy.status_code == 409 and busy.json()['error']['code'] == 'assistant_conversation_busy'
    run_id = first_run.json()['id']
    cancelled = await admin.post('/api/runs/' + run_id + '/cancel', json={})
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    await execute_run(app.state.container, UUID(run_id))
    assert not provider.requests
    detail = (await admin.get(path + '/' + first.json()['id'])).json()
    assert len(detail['turns']) == 1 and detail['turns'][0]['run']['status'] == 'cancelled'
    assert (await admin.post(run_path, json=body)).json()['id'] == run_id


async def test_draft_is_not_formal_use_and_unpublish_revokes_queued_use(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    created = await admin.post('/api/agents', json={'name': 'draft', 'model_id': model_id, 'max_tokens': 32})
    agent_id = created.json()['id']
    path = f'/api/agents/{agent_id}/conversations'
    assert (await admin.post(path, json={'client_request_id': str(uuid4())})).status_code == 409
    await admin.post('/api/agents/' + agent_id + '/publish', json={'expected_version': 1})
    chat = await conversation(admin, agent_id)
    run = await submit(admin, agent_id, chat['id'], 'queued')
    await admin.post('/api/agents/' + agent_id + '/unpublish', json={'expected_version': 2})
    await execute_run(app.state.container, UUID(run['id']))
    failed = (await admin.get('/api/runs/' + run['id'])).json()
    assert failed['status'] == 'failed' and failed['usage']['status'] == 'known'
    assert not provider.requests
    assert (await admin.get(path + '/' + chat['id'])).status_code == 200


async def test_knowledge_bound_assistant_uses_existing_preparation_and_sources(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    from tests.core.integration.test_knowledge import finish_preparation
    from tests.core.integration.test_knowledge_failures import ready_base

    base = await ready_base(admin, app, provider)
    agent = await assistant(admin, model_id, knowledge_base_ids=[base['id']])
    chat = await conversation(admin, agent['id'])
    value = await submit(admin, agent['id'], chat['id'], 'Which port?')
    assert value['kind'] == 'knowledge_preparation'
    during = (await admin.get(f'/api/agents/{agent["id"]}/conversations/{chat["id"]}')).json()
    assert during['preparing_message']['content'] == 'Which port?'
    assert during['conversation']['preparation_id'] == value['id']
    assert during['turns'] == []
    result = await finish_preparation(admin, app, value)
    assert result['status'] == 'admitted', result
    await execute_run(app.state.container, UUID(result['run']['id']))
    detail = (await admin.get(f'/api/agents/{agent["id"]}/conversations/{chat["id"]}')).json()
    assert detail['preparing_message'] is None
    assert detail['turns'][0]['request_id'] == during['preparing_message']['request_id']
    assert detail['turns'][0]['sources']
    assert detail['turns'][0]['run']['status'] == 'completed'
    assert '8080' in str(provider.requests[-1])


async def test_accepted_run_retains_its_publication_while_later_turns_use_the_new_one(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    agent = await assistant(admin, model_id)
    chat = await conversation(admin, agent['id'])
    queued = await submit(admin, agent['id'], chat['id'], 'ACCEPTED BEFORE REPUBLISH')
    deleted = await admin.delete('/api/agents/' + agent['id'], headers={'Content-Type': 'application/json'})
    assert deleted.status_code == 409, deleted.text
    updated = await admin.patch(
        '/api/agents/' + agent['id'],
        json={
            'name': '最新版助手',
            'model_id': model_id,
            'system_prompt': 'NEWLY PUBLISHED',
            'welcome_message': 'LATEST GREETING',
            'max_tokens': 32,
            'expected_version': 2,
        },
    )
    assert updated.status_code == 200, updated.text
    published = await admin.post('/api/agents/' + agent['id'] + '/publish', json={'expected_version': 3})
    assert published.status_code == 200, published.text
    await execute_run(app.state.container, UUID(queued['id']))
    assert provider.requests[-1]['messages'][0]['content'] == 'PUBLISHED SYSTEM'
    later = await submit(admin, agent['id'], chat['id'], 'AFTER REPUBLISH')
    await execute_run(app.state.container, UUID(later['id']))
    assert provider.requests[-1]['messages'][0]['content'] == 'NEWLY PUBLISHED'
    detail = (await admin.get(f'/api/agents/{agent["id"]}/conversations/{chat["id"]}')).json()
    assert detail['conversation']['name'] == '最新版助手'
    assert [row['published_version'] for row in detail['turns']] == [1, 2]


async def test_session_rename_delete_versions_privacy_and_original_request_tombstone(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    from sqlalchemy import select

    from zhiheng.modules.agents.orm import AssistantConversation
    from zhiheng.runtime.orm import Run

    agent = await assistant(admin, model_id)
    root = f'/api/agents/{agent["id"]}/conversations'
    request = {'client_request_id': str(uuid4())}
    original = (await admin.post(root, json=request)).json()
    endpoint = root + '/' + original['id']
    assert original['version'] == 1
    other = await assistant(admin, model_id, name='another')
    wrong = f'/api/agents/{other["id"]}/conversations/{original["id"]}'
    assert (await admin.patch(wrong, json={'title': 'steal', 'expected_version': 1})).status_code == 404
    from tests.core.integration.test_platform_module_permissions import grant

    await grant(app, 'agents')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.patch(endpoint, json={'title': 'steal', 'expected_version': 1})).status_code == 404
        assert (await member.request('DELETE', endpoint, json={'expected_version': 1})).status_code == 404
    for invalid in ('   ', '\x00', 'x' * 101):
        assert (await admin.patch(endpoint, json={'title': invalid, 'expected_version': 1})).status_code == 422
    first, second = await asyncio.gather(
        admin.patch(endpoint, json={'title': 'Research notes', 'expected_version': 1}),
        admin.patch(endpoint, json={'title': 'Parallel edit', 'expected_version': 1}),
    )
    assert sorted((first.status_code, second.status_code)) == [200, 409]
    renamed = first.json() if first.status_code == 200 else second.json()
    assert renamed['version'] == 2
    run = await submit(admin, agent['id'], original['id'], 'FIRST QUESTION')
    busy = await admin.request('DELETE', endpoint, json={'expected_version': 3})
    assert busy.status_code == 409 and busy.json()['error']['code'] == 'assistant_conversation_busy'
    await execute_run(app.state.container, UUID(run['id']))
    detail = (await admin.get(endpoint)).json()
    assert detail['conversation']['title'] == renamed['title']  # Sending the first question keeps a custom title.
    assert detail['turns'][0]['run']['text'] == '你好'
    assert (await admin.request('DELETE', endpoint, json={'expected_version': 2})).status_code == 409
    assert (await admin.request('DELETE', endpoint, json={'expected_version': 3})).status_code == 204
    assert (await admin.get(root)).json() == []
    assert (await admin.get(endpoint)).status_code == 404
    assert (await admin.get('/api/runs/' + run['id'])).status_code == 404
    assert (await admin.get('/api/runs/' + run['id'] + '/events')).status_code == 404
    assert (await admin.post(root, json=request)).status_code == 404  # Original creation ID cannot resurrect it.
    assert (await admin.post(endpoint + '/runs', json={'message': 'again', 'client_request_id': str(uuid4())})).status_code == 404
    async with app.state.container.database.sessions() as session:
        row = await session.get(AssistantConversation, UUID(original['id']))
        assert row is not None and row.deleted_at is not None
        assert await session.scalar(select(Run.id).where(Run.id == UUID(run['id']))) is not None


async def test_deleted_history_cursor_and_active_knowledge_preparation(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    from tests.core.integration.test_knowledge_failures import ready_base

    base = await ready_base(admin, app, provider)
    agent = await assistant(admin, model_id, knowledge_base_ids=[base['id']])
    oldest = await conversation(admin, agent['id'])
    newest = await conversation(admin, agent['id'])
    root = f'/api/agents/{agent["id"]}/conversations'
    assert (await admin.request('DELETE', root + '/' + newest['id'], json={'expected_version': 1})).status_code == 204
    assert [row['id'] for row in (await admin.get(root, params={'before': newest['id']})).json()] == [oldest['id']]
    preparation = await submit(admin, agent['id'], oldest['id'], 'Which port?')
    assert preparation['kind'] == 'knowledge_preparation'
    assert (await admin.request('DELETE', root + '/' + oldest['id'], json={'expected_version': 1})).status_code == 409
