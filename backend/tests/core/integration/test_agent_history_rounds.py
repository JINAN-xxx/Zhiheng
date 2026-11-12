import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_agents import config
from tests.core.integration.test_knowledge import finish_preparation
from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.integration.test_public_protocol import drive, provision
from tests.core.integration.test_work import published, task
from tests.core.provider import ProviderServer
from zhiheng.modules.agents.orm import Agent, AgentVersion
from zhiheng.modules.work.orm import WorkMessage
from zhiheng.worker.execution import execute_run


def conversation() -> list[dict[str, str]]:
    return [{'role': role, 'content': f'{index}:{role}'} for index in range(22) for role in ('user', 'assistant')] + [
        {'role': 'user', 'content': 'current question'}
    ]


async def test_legacy_draft_and_publication_json_read_as_twenty_without_migration(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI
) -> None:
    agent = (await admin.post('/api/agents', json=config(model_id))).json()
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(Agent, UUID(agent['id']))
        legacy = {key: value for key, value in row.config.items() if key != 'history_rounds'}
        row.config = legacy
        row.published_version = row.publication_count = 1
        session.add(AgentVersion(agent_id=row.id, version=1, draft_version=1, config=legacy, skills=[]))
    response = await admin.get(f'/api/agents/{agent["id"]}')
    assert response.status_code == 200, response.text
    assert response.json()['history_rounds'] == response.json()['published_config']['history_rounds'] == 20


@pytest.mark.parametrize('rounds', [0, 1, 20])
async def test_preview_history_rounds_reaches_provider_and_replay_keeps_snapshot(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer, rounds: int
) -> None:
    agent = (
        await admin.post('/api/agents', json=config(model_id, history_rounds=rounds, system_prompt='TRUSTED'))
    ).json()
    assert agent.get('history_rounds') == rounds, agent
    endpoint = f'/api/agents/{agent["id"]}/preview'
    body = {'client_request_id': str(uuid4()), 'messages': conversation()}
    admitted = await admin.post(endpoint, json=body)
    assert admitted.status_code == 202, admitted.text
    edited = await admin.patch(
        f'/api/agents/{agent["id"]}', json=config(model_id, expected_version=1, history_rounds=20 - rounds)
    )
    assert edited.status_code == 200, edited.text
    assert (await admin.post(endpoint, json=body)).json()['id'] == admitted.json()['id']
    changed = {**body, 'messages': [{**item} for item in body['messages']]}
    changed['messages'][0]['content'] = 'changed even if discarded'
    assert (await admin.post(endpoint, json=changed)).status_code == 409
    await execute_run(app.state.container, UUID(admitted.json()['id']))
    assert provider.requests[-1]['messages'] == [
        {'role': 'system', 'content': 'TRUSTED'},
        *body['messages'][-(rounds * 2 + 1) :],
    ]


@pytest.mark.parametrize('rounds', [0, 1, 20])
async def test_public_history_rounds_uses_frozen_publication_while_debug_uses_draft(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer, rounds: int
) -> None:
    draft_rounds = 20 if rounds == 0 else 0
    application, key = await provision(admin, model_id)
    agent = (
        await admin.post('/api/agents', json=config(model_id, application_id=application, history_rounds=rounds))
    ).json()
    published_response = await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})
    assert published_response.status_code == 200, published_response.text
    edited = await admin.patch(
        f'/api/agents/{agent["id"]}',
        json=config(model_id, application_id=application, history_rounds=draft_rounds, expected_version=2),
    )
    assert edited.json()['history_rounds'] == draft_rounds
    assert edited.json()['published_config']['history_rounds'] == rounds
    body = {'messages': conversation(), 'client_request_id': str(uuid4())}
    debug = await admin.post(f'/api/agents/{agent["id"]}/preview', json=body)
    assert debug.status_code == 202, debug.text
    await execute_run(app.state.container, UUID(debug.json()['id']))
    assert provider.requests[-1]['messages'] == body['messages'][-(draft_rounds * 2 + 1) :]
    response = await drive(
        app.state.container,
        asyncio.create_task(
            admin.post(
                f'/v1/agents/{agent["id"]}/chat/completions',
                headers={'Authorization': 'Bearer ' + key['api_key']},
                json=body,
            )
        ),
    )
    assert response.status_code == 200, response.text
    assert provider.requests[-1]['messages'] == body['messages'][-(rounds * 2 + 1) :]


@pytest.mark.parametrize('rounds', [0, 1, 20])
async def test_work_history_rounds_uses_frozen_member_keeps_goal_attribution_and_full_history(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer, rounds: int
) -> None:
    agent = await published(admin, model_id, history_rounds=rounds, system_prompt='FROZEN SYSTEM')
    work = await task(admin, agent, goal='PERSISTENT TASK GOAL')
    edit = await admin.patch(
        f'/api/agents/{agent["id"]}',
        json=config(model_id, application_id=agent['application_id'], history_rounds=20 - rounds, expected_version=2),
    )
    assert edit.status_code == 200, edit.text
    assert (await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 3})).status_code == 200
    async with app.state.container.database.sessions.begin() as session:
        for index, message in enumerate(conversation()[:-1], 2):
            session.add(
                WorkMessage(
                    task_id=UUID(work['id']),
                    client_request_id=uuid4(),
                    sequence=index,
                    role=message['role'],
                    content=message['content'],
                    status='completed',
                    agent_id=UUID(agent['id']),
                    agent_name_snapshot='Frozen author',
                    published_version=1,
                )
            )
    endpoint = f'/api/work/tasks/{work["id"]}'
    admitted = await admin.post(
        endpoint + '/runs',
        json={
            'agent_id': agent['id'],
            'message': 'current question',
            'client_request_id': str(uuid4()),
            'expected_version': work['version'],
        },
    )
    assert admitted.status_code == 202, admitted.text
    await execute_run(app.state.container, UUID(admitted.json()['id']))
    messages = provider.requests[-1]['messages']
    assert messages[0] == {'role': 'system', 'content': 'FROZEN SYSTEM'}
    assert 'PERSISTENT TASK GOAL' in messages[1]['content']
    history = [message for message in messages if message['role'] != 'system']
    assert len(history) == rounds * 2 + 1
    assert history[-1] == {'role': 'user', 'content': 'current question'}
    if rounds:
        assert history[0]['content'] == f'{22 - rounds}:user'
        assert history[-2]['content'] == '[Agent: Frozen author; publishedVersion: 1]\n21:assistant'
    assert len((await admin.get(endpoint)).json()['messages']) == 47


async def test_zero_history_keeps_skills_and_knowledge_at_provider(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    agent = (
        await admin.post(
            '/api/agents',
            json=config(
                model_id,
                history_rounds=0,
                system_prompt='TRUSTED SYSTEM',
                skill_slugs=['document-summary'],
                knowledge_base_ids=[base['id']],
            ),
        )
    ).json()
    response = await admin.post(
        f'/api/agents/{agent["id"]}/preview',
        json={
            'messages': conversation(),
            'client_request_id': str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    result = await finish_preparation(admin, app, response.json())
    assert result['run']['status'] == 'completed', result
    messages = provider.requests[-1]['messages']
    assert {'role': 'system', 'content': 'TRUSTED SYSTEM'} in messages
    assert any(item['role'] == 'system' and '文档总结' in item['content'] for item in messages)
    assert len(messages) == 4 and messages[-1]['role'] == 'user'
    assert 'current question' in messages[-1]['content'] and '8080' in messages[-1]['content']
    assert '21:assistant' not in str(messages)
