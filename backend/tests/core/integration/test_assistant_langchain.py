"""Real official Agent/Saver over a controlled HTTP model and isolated databases."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from tests.core.chat_support import wire_messages
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.agents.orm import AgentRunAudit, AssistantConversation, AssistantSummary
from zhiheng.modules.billing.orm import Invocation
from zhiheng.worker.execution import execute_run

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests.core.provider import ProviderServer


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return Settings.model_validate(
        {
            **settings.model_dump(),
            'provider_timeout_seconds': 5,
            'chat_summary_trigger_tokens': 512,
            'chat_summary_keep_tokens': 128,
        }
    )


async def assistant(admin: httpx.AsyncClient, model_id: str, **changes: object) -> dict:
    value = await admin.post(
        '/api/agents',
        json={
            'name': '最新配置助手',
            'model_id': model_id,
            'system_prompt': 'ORIGINAL SYSTEM',
            'max_tokens': 32,
            **changes,
        },
    )
    assert value.status_code == 201, value.text
    enabled = await admin.post(
        f'/api/agents/{value.json()["id"]}/enable', json={'expected_version': value.json()['version']}
    )
    assert enabled.status_code == 200, enabled.text
    return enabled.json()


async def conversation(admin: httpx.AsyncClient, aid: str) -> dict:
    result = await admin.post(f'/api/agents/{aid}/conversations', json={'client_request_id': str(uuid4())})
    assert result.status_code == 201, result.text
    return result.json()


async def send(admin: httpx.AsyncClient, aid: str, cid: str, message: str) -> dict:
    result = await admin.post(
        f'/api/agents/{aid}/conversations/{cid}/runs', json={'client_request_id': str(uuid4()), 'message': message}
    )
    assert result.status_code == 202, result.text
    return result.json()


async def execute(app: FastAPI, run_id: str) -> None:
    parent = asyncio.create_task(execute_run(app.state.container, UUID(run_id)))
    children = set()

    async def drain() -> None:
        while not parent.done():
            async with app.state.container.database.sessions() as session:
                link = await session.get(AssistantSummary, UUID(run_id))
                child = link.run_id if link else None
            if child and child not in children:
                children.add(child)
                await execute_run(app.state.container, child)
            await asyncio.sleep(0.01)
        await parent

    try:
        await asyncio.wait_for(drain(), 15)
    finally:
        if not parent.done():
            parent.cancel()
            await asyncio.gather(parent, return_exceptions=True)


async def test_latest_configuration_has_no_publication_catalog_and_memory_is_incremental(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    agent = await assistant(admin, model_id)
    assert agent['status'] == 'enabled' and 'published_config' not in agent and 'published_version' not in agent
    assert 'history_rounds' not in agent
    chat = await conversation(admin, agent['id'])
    first = await send(admin, agent['id'], chat['id'], 'FIRST QUESTION')
    edit = await admin.patch(
        f'/api/agents/{agent["id"]}',
        json={
            'name': '改名后助手',
            'model_id': model_id,
            'system_prompt': 'LATEST SYSTEM',
            'max_tokens': 32,
            'expected_version': agent['version'],
        },
    )
    assert edit.status_code == 200 and edit.json()['status'] == 'enabled', edit.text
    await execute(app, first['id'])
    assert provider.requests[-1]['messages'][0]['content'] == 'ORIGINAL SYSTEM'
    async with app.state.container.database.sessions() as session:
        row = await session.get(AssistantConversation, UUID(chat['id']))
        first_head = row.checkpoint_id
    assert first_head
    second = await send(admin, agent['id'], chat['id'], 'SECOND QUESTION')
    await execute(app, second['id'])
    assert provider.requests[-1]['messages'][0]['content'] == 'LATEST SYSTEM'
    assert wire_messages(provider) == [('user', 'FIRST QUESTION'), ('assistant', '你好'), ('user', 'SECOND QUESTION')]
    assert len(provider.requests) == 2
    async with app.state.container.database.sessions() as session:
        row = await session.get(AssistantConversation, UUID(chat['id']))
        audit = await session.get(AgentRunAudit, UUID(second['id']))
        assert row.checkpoint_id and row.checkpoint_id != first_head
        assert audit.execution['incremental'] and audit.execution['base_checkpoint_id'] == first_head
        assert audit.execution['completion']['context_model_input_tokens'] > 0
        assert audit.config['system_prompt'] == 'LATEST SYSTEM'


async def test_preview_is_stateless_and_uses_same_agent_without_server_memory(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    agent = await assistant(admin, model_id)
    for question in ['PREVIEW ONE', 'PREVIEW TWO']:
        run = await admin.post(
            f'/api/agents/{agent["id"]}/preview',
            json={'client_request_id': str(uuid4()), 'messages': [{'role': 'user', 'content': question}]},
        )
        assert run.status_code == 202, run.text
        await execute(app, run.json()['id'])
        result = (await admin.get(f'/api/runs/{run.json()["id"]}')).json()
        assert result['status'] == 'completed', result
    assert wire_messages(provider) == [('user', 'PREVIEW TWO')]
    assert len(provider.requests) == 2


async def test_summary_uses_separate_native_run_and_advances_only_effective_memory(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    agent = await assistant(admin, model_id)
    chat = await conversation(admin, agent['id'])
    first = await send(admin, agent['id'], chat['id'], 'A' * 600)
    await execute(app, first['id'])
    second = await send(admin, agent['id'], chat['id'], 'B' * 600)
    await execute(app, second['id'])
    result = (await admin.get(f'/api/runs/{second["id"]}')).json()
    assert result['status'] == 'completed', result
    async with app.state.container.database.sessions() as session:
        link = await session.get(AssistantSummary, UUID(second['id']))
        audit = await session.get(AgentRunAudit, UUID(second['id']))
        row = await session.get(AssistantConversation, UUID(chat['id']))
        assert link and link.parent_attempt_id
        assert audit.execution['summary_used'] and audit.execution['completion']['summary_count'] == 1
        assert row.memory_run_id == UUID(second['id'])
        assert await session.scalar(select(func.count()).select_from(Invocation)) == 3
    assert len(provider.requests) == 3


async def test_disabled_assistant_rejects_queued_execution_without_upstream_call(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    agent = await assistant(admin, model_id)
    chat = await conversation(admin, agent['id'])
    run = await send(admin, agent['id'], chat['id'], 'QUEUED')
    disabled = await admin.post(f'/api/agents/{agent["id"]}/disable', json={'expected_version': agent['version']})
    assert disabled.status_code == 200, disabled.text
    await execute(app, run['id'])
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'failed' and not provider.requests, result


async def test_retrieval_context_is_temporary_and_removed_historical_sources_block_reuse(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, settings: Settings
) -> None:
    from tests.core.integration.test_knowledge import finish_preparation
    from tests.core.integration.test_knowledge_failures import ready_base
    from zhiheng.infrastructure.agents.adapters.checkpoints import CheckpointStore

    base = await ready_base(admin, app, provider)
    agent = await assistant(admin, model_id, knowledge_base_ids=[base['id']])
    chat = await conversation(admin, agent['id'])
    preparing = await send(admin, agent['id'], chat['id'], 'QUESTION WITHOUT KNOWLEDGE PLAINTEXT')
    result = await finish_preparation(admin, app, preparing)
    assert result['run']['status'] == 'completed', result
    assert '8080' in str(provider.requests[-1]['messages'])
    async with app.state.container.database.sessions() as session:
        row = await session.get(AssistantConversation, UUID(chat['id']))
        head = row.checkpoint_id
    store = CheckpointStore(settings.database_url.get_secret_value(), 'assistant_checkpoints')
    thread_id = app.state.container.assistant_agent.thread(UUID(agent['owner_id']), UUID(agent['id']), UUID(chat['id']))
    async with store.saver() as saver:
        state = await saver.aget_tuple(store.config(thread_id, head))
    messages = state.checkpoint['channel_values']['messages']
    assert messages[0].content == 'QUESTION WITHOUT KNOWLEDGE PLAINTEXT'
    assert '8080' not in str([m.content for m in messages])
    changed = await admin.patch(
        f'/api/agents/{agent["id"]}',
        json={
            'name': agent['name'],
            'model_id': model_id,
            'max_tokens': 32,
            'knowledge_base_ids': [],
            'expected_version': agent['version'],
        },
    )
    assert changed.status_code == 200, changed.text
    prior = len(provider.requests)
    denied = await admin.post(
        f'/api/agents/{agent["id"]}/conversations/{chat["id"]}/runs',
        json={'client_request_id': str(uuid4()), 'message': 'do not resend removed source'},
    )
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'assistant_history_source_unavailable', (
        denied.text
    )
    assert len(provider.requests) == prior


async def test_checkpoint_failure_preserves_raw_usage_without_advancing_memory_or_replaying(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zhiheng.infrastructure.agents.adapters.agent import AgentProvider
    from zhiheng.shared.errors import AppError

    agent = await assistant(admin, model_id)
    chat = await conversation(admin, agent['id'])
    run = await send(admin, agent['id'], chat['id'], 'FAILED SAVE')
    original = AgentProvider._checkpoint_result

    async def failed(*args: object, **kwargs: object) -> None:
        raise AppError('checkpoint_write_failed', 'injected saver failure', 503)

    monkeypatch.setattr(AgentProvider, '_checkpoint_result', failed)
    await execute(app, run['id'])
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'failed' and result['text'] == '你好', result
    assert result['usage'] == {'input_tokens': 8, 'output_tokens': 2, 'status': 'known'}
    async with app.state.container.database.sessions() as session:
        row = await session.get(AssistantConversation, UUID(chat['id']))
        assert row.checkpoint_id is None and row.memory_run_id is None
    assert len(provider.requests) == 1
    monkeypatch.setattr(AgentProvider, '_checkpoint_result', original)
    next_run = await send(admin, agent['id'], chat['id'], 'NEXT QUESTION')
    await execute(app, next_run['id'])
    assert wire_messages(provider) == [('user', 'NEXT QUESTION')]
    assert len(provider.requests) == 2


async def test_cancelled_reasoning_retains_unknown_usage_and_no_effective_checkpoint(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'reasoning-silent'})
    agent = await assistant(admin, model_id)
    chat = await conversation(admin, agent['id'])
    run = await send(admin, agent['id'], chat['id'], 'CANCEL ME')
    task = asyncio.create_task(execute_run(app.state.container, UUID(run['id'])))
    for _ in range(150):
        result = (await admin.get(f'/api/runs/{run["id"]}')).json()
        if result.get('reasoning'):
            break
        await asyncio.sleep(0.02)
    assert result.get('reasoning') == '先思考', result
    assert (await admin.post(f'/api/runs/{run["id"]}/cancel', json={})).status_code == 200
    await asyncio.wait_for(task, 3)
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'cancelled' and result['usage']['status'] == 'unknown', result
    async with app.state.container.database.sessions() as session:
        row = await session.get(AssistantConversation, UUID(chat['id']))
        assert row.checkpoint_id is None and row.memory_run_id is None
    assert len(provider.requests) == 1


async def test_old_conversation_is_readable_but_requires_new_memory_session(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    agent = await assistant(admin, model_id)
    chat = await conversation(admin, agent['id'])
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(AssistantConversation, UUID(chat['id']))
        row.runtime_version = None
    detail = await admin.get(f'/api/agents/{agent["id"]}/conversations/{chat["id"]}')
    assert detail.status_code == 200 and detail.json()['conversation']['memory_available'] is False
    denied = await admin.post(
        f'/api/agents/{agent["id"]}/conversations/{chat["id"]}/runs',
        json={'client_request_id': str(uuid4()), 'message': 'legacy must not fall back'},
    )
    assert denied.status_code == 409 and denied.json()['error']['code'] == 'assistant_memory_legacy', denied.text


async def test_deletion_cleans_only_sdk_state_and_keeps_run_and_usage_facts(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    from sqlalchemy import text

    from zhiheng.runtime.orm import Run

    agent = await assistant(admin, model_id)
    chat = await conversation(admin, agent['id'])
    run = await send(admin, agent['id'], chat['id'], 'TO BE DELETED')
    await execute(app, run['id'])
    path = f'/api/agents/{agent["id"]}/conversations/{chat["id"]}'
    detail = (await admin.get(path)).json()
    deleted = await admin.request('DELETE', path, json={'expected_version': detail['conversation']['version']})
    assert deleted.status_code == 204, deleted.text
    await app.state.container.assistant_agent.cleanup_checkpoints()
    async with app.state.container.database.sessions() as session:
        assert await session.get(Run, UUID(run['id']))
        assert await session.scalar(select(Invocation).where(Invocation.run_id == UUID(run['id'])))
        assert await session.scalar(text('SELECT count(*) FROM assistant_checkpoints.checkpoints')) == 0


async def test_cancel_during_summary_cancels_child_without_starting_main_or_changing_head(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    from zhiheng.runtime.orm import Run

    agent = await assistant(admin, model_id)
    chat = await conversation(admin, agent['id'])
    first = await send(admin, agent['id'], chat['id'], 'A' * 600)
    await execute(app, first['id'])
    async with app.state.container.database.sessions() as session:
        original_head = (await session.get(AssistantConversation, UUID(chat['id']))).checkpoint_id
    second = await send(admin, agent['id'], chat['id'], 'B' * 600)
    task = asyncio.create_task(execute_run(app.state.container, UUID(second['id'])))
    child_id = None
    try:
        for _ in range(250):
            async with app.state.container.database.sessions() as session:
                link = await session.get(AssistantSummary, UUID(second['id']))
                child_id = link.run_id if link else None
            if child_id:
                break
            await asyncio.sleep(0.01)
        assert child_id is not None
        response = await admin.post(f'/api/runs/{second["id"]}/cancel', json={})
        assert response.status_code == 200, response.text
        await asyncio.wait_for(task, 3)
        await execute_run(app.state.container, child_id)
        async with app.state.container.database.sessions() as session:
            parent = await session.get(Run, UUID(second['id']))
            child = await session.get(Run, child_id)
            row = await session.get(AssistantConversation, UUID(chat['id']))
            assert parent.status == child.status == 'cancelled'
            assert not parent.provider_started and not child.provider_started
            assert row.checkpoint_id == original_head
        assert len(provider.requests) == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_queued_public_call_rechecks_activation_and_preserves_original_key(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    from tests.core.integration.test_agents import issue_agent_key
    from tests.core.integration.test_public_protocol import provision
    from zhiheng.modules.agents.public import AgentPublicRequest
    from zhiheng.modules.billing.orm import Invocation

    application, _ = await provision(admin, model_id)
    agent = await assistant(admin, model_id, application_id=application)
    key = await issue_agent_key(admin, agent)
    queued, _, _ = await app.state.container.agent_runs.submit_public(
        key['api_key'],
        UUID(agent['id']),
        AgentPublicRequest(user='test-user', message='QUEUED API'),
        uuid4(),
    )
    disabled = await admin.post(f'/api/agents/{agent["id"]}/disable', json={'expected_version': agent['version']})
    assert disabled.status_code == 200, disabled.text
    await execute(app, str(queued.id))
    result = (await admin.get(f'/api/runs/{queued.id}')).json()
    assert result['status'] == 'failed' and not provider.requests, result
    async with app.state.container.database.sessions() as session:
        usage = await session.scalar(select(Invocation).where(Invocation.run_id == queued.id))
        assert str(usage.key_id) == key['id']
