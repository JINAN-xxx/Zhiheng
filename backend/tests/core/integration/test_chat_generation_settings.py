from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update

from tests.core.integration.test_knowledge import finish_preparation
from tests.core.integration.test_knowledge_failures import ready_base, source
from tests.core.integration.test_skills import install
from tests.core.provider import ProviderServer
from zhiheng.modules.chat.orm import ChatMessage
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


@pytest.mark.parametrize('with_dependencies', [False, True])
async def test_generation_controls_reach_provider_quota_history_and_regeneration(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    with_dependencies: bool,
) -> None:
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    path = f'/api/chat/conversations/{conversation}/runs'
    settings = {'system_prompt': 'PRIVATE 用户指令', 'temperature': 0.25, 'max_output_tokens': 24}
    body = {'model_id': model_id, 'message': 'hello', 'client_request_id': str(uuid4()), **settings}
    if with_dependencies:
        skill = await install(admin, 'generation-test', 'SELECTED SKILL')
        attachment = await source(admin, 'context.txt', b'ATTACHMENT CONTENT')
        body.update(skill_slugs=[skill['slug']], file_ids=[attachment['id']])
    accepted = await admin.post(path, json=body)
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()['id']
    async with app.state.container.database.sessions() as session:
        frozen = await session.get(Run, UUID(run_id))
        request = frozen.payload
    assert request['binding']['max_output_tokens'] == 24
    assert request['binding']['options']['temperature'] == 0.25
    expected_reservation = sum(len(message['content'].encode()) + 16 for message in request['messages']) + 24
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] == expected_reservation
    duplicate = await admin.post(path, json=body)
    assert duplicate.status_code == 202 and duplicate.json()['id'] == run_id
    for changes in ({'system_prompt': 'changed'}, {'temperature': 0}, {'max_output_tokens': 23}):
        conflict = await admin.post(path, json={**body, **changes})
        assert conflict.status_code == 409, conflict.text
    await execute_run(app.state.container, UUID(run_id))
    outgoing = provider.requests[-1]
    assert outgoing['temperature'] == 0.25
    assert outgoing.get('max_tokens', outgoing.get('max_completion_tokens')) == 24
    assert outgoing['messages'][0] == {'role': 'system', 'content': settings['system_prompt']}
    if with_dependencies:
        assert 'SELECTED SKILL' in outgoing['messages'][1]['content']
        assert 'ATTACHMENT CONTENT' in outgoing['messages'][-1]['content']
    messages = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    assert all(message['generation_config'] == {'model_id': model_id, **settings} for message in messages)
    endpoint = f'/api/chat/conversations/{conversation}/messages/{messages[-1]["id"]}/regenerate'
    regenerated = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert regenerated.status_code == 202, regenerated.text
    await execute_run(app.state.container, UUID(regenerated.json()['id']))
    assert provider.requests[-1] == outgoing
    changed = await admin.patch(f'/api/models/{model_id}', json={'max_output_tokens': 16})
    assert changed.status_code == 200, changed.text
    denied = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert denied.status_code == 422, denied.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get(f'/api/chat/conversations/{conversation}/messages')).status_code == 404
    preview = (await admin.get(f'/api/chat/messages/{messages[-1]["id"]}/share-preview')).json()
    share = await admin.post(
        f'/api/chat/messages/{messages[-1]["id"]}/shares',
        json={
            'include_question': True,
            'expires_in_days': 7,
            'expected_content_hash': preview['content_hash'],
        },
    )
    assert share.status_code == 201, share.text
    shared = await admin.get(f'/api/shared/messages/{share.json()["token"]}')
    assert shared.status_code == 200, shared.text
    assert settings['system_prompt'] not in shared.text and 'generation_config' not in shared.text


async def test_knowledge_preparation_freezes_controls_and_restores_them_on_regeneration(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    base = await ready_base(admin, app, provider)
    skill = await install(admin, 'knowledge-generation-test', 'KNOWLEDGE SELECTED SKILL')
    attachment = await source(admin, 'extra.txt', b'KNOWLEDGE ATTACHMENT')
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    body = {
        'model_id': model_id,
        'message': 'Which port?',
        'client_request_id': str(uuid4()),
        'knowledge_base_ids': [base['id']],
        'skill_slugs': [skill['slug']],
        'file_ids': [attachment['id']],
        'system_prompt': 'PRIVATE KNOWLEDGE INSTRUCTION',
        'temperature': 0.4,
        'max_output_tokens': 32,
    }
    path = f'/api/chat/conversations/{conversation}/runs'
    preparation = await admin.post(path, json=body)
    assert preparation.status_code == 202, preparation.text
    async with app.state.container.database.sessions() as session:
        state = await app.state.container.knowledge_preparation.store.find_state(
            session, UUID(preparation.json()['id'])
        )
    assert {key: state.request[key] for key in ('system_prompt', 'temperature', 'max_output_tokens')} == {
        'system_prompt': body['system_prompt'],
        'temperature': 0.4,
        'max_output_tokens': 32,
    }
    replay = await admin.post(path, json=body)
    assert replay.json()['id'] == preparation.json()['id']
    assert (await admin.post(path, json={**body, 'temperature': 0.5})).status_code == 409
    result = await finish_preparation(admin, app, preparation.json())
    await execute_run(app.state.container, UUID(result['run']['id']))
    outgoing = provider.requests[-1]
    assert outgoing['messages'][0] == {'role': 'system', 'content': body['system_prompt']}
    assert outgoing['temperature'] == 0.4
    assert outgoing.get('max_tokens', outgoing.get('max_completion_tokens')) == 32
    assert '[知识资料]' in outgoing['messages'][-1]['content']
    assert 'KNOWLEDGE SELECTED SKILL' in outgoing['messages'][2]['content']
    assert 'KNOWLEDGE ATTACHMENT' in outgoing['messages'][-1]['content']
    messages = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    assert messages[-1]['generation_config'] == {
        'model_id': model_id,
        'system_prompt': body['system_prompt'],
        'temperature': 0.4,
        'max_output_tokens': 32,
    }
    endpoint = f'/api/chat/conversations/{conversation}/messages/{messages[-1]["id"]}/regenerate'
    regenerated = await admin.post(endpoint, json={'client_request_id': str(uuid4())})
    assert regenerated.status_code == 202, regenerated.text
    second = await finish_preparation(admin, app, regenerated.json())
    await execute_run(app.state.container, UUID(second['run']['id']))
    assert provider.requests[-1] == outgoing


async def test_old_context_has_no_invented_generation_configuration(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    run = await admin.post(
        f'/api/chat/conversations/{conversation}/runs',
        json={
            'model_id': model_id,
            'message': 'hello',
            'client_request_id': str(uuid4()),
        },
    )
    assert run.status_code == 202, run.text
    await execute_run(app.state.container, UUID(run.json()['id']))
    fresh_messages = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    assert all(
        message['generation_config']
        == {
            'model_id': model_id,
            'system_prompt': '',
            'temperature': None,
            'max_output_tokens': None,
        }
        for message in fresh_messages
    )
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.conversation_id == UUID(conversation))
            .values(
                request_context={'model_id': model_id, 'skill_slugs': [], 'file_ids': [], 'knowledge_base_ids': []},
            )
        )
    messages = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    assert all(message.get('generation_config') is None for message in messages)


async def test_output_catalog_and_prompt_budget_rejections_leave_no_run_or_quota(
    admin: httpx.AsyncClient,
    model_id: str,
    provider: ProviderServer,
) -> None:
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    path = f'/api/chat/conversations/{conversation}/runs'
    body = {'model_id': model_id, 'message': 'hello', 'client_request_id': str(uuid4())}
    too_many = await admin.post(path, json={**body, 'max_output_tokens': 65})
    assert too_many.status_code == 422 and too_many.json()['error']['code'] == 'invalid_max_output_tokens'
    too_long = await admin.post(path, json={**body, 'system_prompt': '中' * 32000})
    assert too_long.status_code == 400 and too_long.json()['error']['code'] == 'context_too_long'
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] == 0
    assert (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json() == []
    assert not provider.requests
