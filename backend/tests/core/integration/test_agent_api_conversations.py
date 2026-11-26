"""The public assistant API uses the same durable memory as web conversations."""

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.chat_support import wire_messages
from tests.core.integration.test_agents import issue_agent_key
from tests.core.integration.test_assistant_langchain import assistant, execute
from tests.core.integration.test_public_protocol import drive, provision
from tests.core.provider import ProviderServer
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.agents.orm import AgentRunAudit, AssistantConversation, AssistantSummary
from zhiheng.modules.billing.orm import Invocation
from zhiheng.runtime.orm import Run


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return Settings.model_validate(
        {
            **settings.model_dump(),
            'chat_summary_trigger_tokens': 512,
            'chat_summary_keep_tokens': 128,
        }
    )


async def test_api_conversation_reuses_history_and_keeps_owners_isolated(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    application, _ = await provision(admin, model_id)
    agent = await assistant(admin, model_id, application_id=application)
    aid = agent['id']
    key = await issue_agent_key(admin, agent)
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    path = f'/v1/agents/{aid}/chat/completions'
    first = {'user': 'customer-one', 'message': 'FIRST QUESTION', 'client_request_id': str(uuid4())}
    old = await admin.post(path, headers=headers, json={'messages': [{'role': 'user', 'content': 'hello'}]})
    assert old.status_code == 400 and old.json()['error']['code'] == 'invalid_request', old.text
    result = await drive(app.state.container, asyncio.create_task(admin.post(path, headers=headers, json=first)))
    assert result.status_code == 200, result.text
    cid = result.json()['conversation_id']
    repeated = await admin.post(path, headers=headers, json=first)
    assert repeated.status_code == 200 and repeated.json()['id'] == result.json()['id']
    assert repeated.json()['conversation_id'] == cid
    second = {
        **first,
        'conversation_id': cid,
        'message': 'SECOND QUESTION',
        'client_request_id': str(uuid4()),
        'stream': True,
    }
    foreign = await admin.post(path, headers=headers, json={**second, 'user': 'customer-two'})
    assert foreign.status_code == 404, foreign.text
    assert (await admin.get(f'/api/agents/{aid}/conversations')).json() == []
    assert (await admin.get(f'/api/agents/{aid}/conversations/{cid}')).status_code == 404
    streamed = await drive(app.state.container, asyncio.create_task(admin.post(path, headers=headers, json=second)))
    assert streamed.headers['content-type'].startswith('text/event-stream'), streamed.text
    pieces = [line[6:] for line in streamed.text.splitlines() if line.startswith('data: ')]
    assert pieces[-1] == '[DONE]'
    assert all(json.loads(piece)['conversation_id'] == cid for piece in pieces[:-1])
    assert wire_messages(provider) == [
        ('user', 'FIRST QUESTION'),
        ('assistant', '你好'),
        ('user', 'SECOND QUESTION'),
    ]
    assert len(provider.requests) == 2


async def test_api_conversation_uses_same_summary_and_charges_its_original_key(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    from sqlalchemy import select

    application, _ = await provision(admin, model_id)
    agent = await assistant(admin, model_id, application_id=application)
    key = await issue_agent_key(admin, agent)
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    path = f'/v1/agents/{agent["id"]}/chat/completions'
    first = {'user': 'customer-one', 'message': 'A' * 600, 'client_request_id': str(uuid4())}
    first_reply = await drive(app.state.container, asyncio.create_task(admin.post(path, headers=headers, json=first)))
    assert first_reply.status_code == 200, first_reply.text
    cid = UUID(first_reply.json()['conversation_id'])
    second = {**first, 'message': 'B' * 600, 'conversation_id': str(cid), 'client_request_id': str(uuid4())}
    async with asyncio.TaskGroup() as group:
        response = group.create_task(admin.post(path, headers=headers, json=second))
        async with asyncio.timeout(8):
            while True:
                async with app.state.container.database.sessions() as session:
                    run = await session.scalar(select(Run.id).where(Run.status == 'pending'))
                if run is not None:
                    break
                await asyncio.sleep(0.01)
        assert not response.done()
        concurrent = await admin.post(path, headers=headers, json={**second, 'client_request_id': str(uuid4())})
        assert concurrent.status_code == 409, concurrent.text
        await execute(app, str(run))
        second_reply = await asyncio.wait_for(response, 10)
    assert second_reply.status_code == 200, second_reply.text
    async with app.state.container.database.sessions() as session:
        link = await session.get(AssistantSummary, run)
        assert link is not None and link.conversation_id == cid
        audit = await session.get(AgentRunAudit, run)
        assert audit is not None and audit.execution['summary_used']
        row = await session.get(AssistantConversation, cid)
        assert row is not None and row.memory_run_id == run
        usage = await session.scalar(select(Invocation).where(Invocation.run_id == link.run_id))
        assert usage is not None and usage.key_id == UUID(key['id'])
    assert len(provider.requests) == 3
