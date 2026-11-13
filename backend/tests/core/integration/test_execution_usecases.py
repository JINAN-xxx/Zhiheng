"""Shared admission and execution policies retain native transactions and accounting."""

from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text

from tests.core.billing_support import enable_free_billing
from tests.core.integration.test_chat_context_memory import seed_history
from tests.core.integration.test_chat_runs import admit
from tests.core.provider import ProviderServer
from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.shared.errors import AppError
from zhiheng.worker.execution import execute_run


async def test_chat_model_test_and_summary_share_run_admission(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = app.state.container
    assert hasattr(container, 'run_admission'), 'A shared application admission service is required'
    assert container.chat_context.admission is container.run_admission
    assert container.chat_context.runs is container.runs
    assert container.chat_context.usage is container.usage
    assert container.chat_context.admission is not container.admission
    admitted: list[str] = []
    reserve = container.run_admission.reserve

    async def record(*args: object, **kwargs: object) -> object:
        result = await reserve(*args, **kwargs)
        admitted.append(str(kwargs['resource_type']))
        return result

    monkeypatch.setattr(container.run_admission, 'reserve', record)
    await seed_history(admin, app, model_id)
    result = await admin.post(f'/api/models/{model_id}/test', json={'client_request_id': str(uuid4())})
    assert result.status_code == 202, result.text
    assert set(admitted) == {'chat', 'chat_summary', 'connection_test'}


async def test_execution_authorization_rechecks_chat_owner_state(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    container = app.state.container
    assert hasattr(container, 'execution_authorization'), 'Execution authorization must be independently callable'
    conversation_id, run = await admit(admin, model_id)
    async with container.database.sessions.begin() as session:
        conversation = await session.get(Conversation, UUID(conversation_id))
        assert conversation is not None
        conversation.archived = True
        claim = await container.runs.claim(session, UUID(run['id']), 60)
        assert claim is not None
    with pytest.raises(AppError) as rejected:
        await container.execution_authorization.require(claim)
    assert rejected.value.code == 'conversation_archived'
    assert not provider.requests


async def test_worker_denial_releases_reservation_before_provider(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    container = app.state.container
    assert hasattr(container, 'execution_authorization'), 'Worker must call the application authorization service'
    conversation_id, run = await admit(admin, model_id)
    async with container.database.sessions.begin() as session:
        conversation = await session.get(Conversation, UUID(conversation_id))
        assert conversation is not None
        conversation.archived = True
    await execute_run(container, UUID(run['id']))
    snapshot = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert snapshot['status'] == 'failed' and snapshot['usage']['status'] == 'known'
    usage = (await admin.get('/api/usage')).json()
    assert usage['reserved_tokens'] == 0 and usage['used_tokens'] == 0
    assert not provider.requests


@pytest.mark.parametrize('provider_started', [False, True])
async def test_application_recovery_projects_partial_chat_and_preserves_usage_policy(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider_started: bool
) -> None:
    await enable_free_billing(admin)
    container = app.state.container
    assert hasattr(container, 'run_recovery'), 'Recovery must be independently callable from application'
    conversation_id, original = await admit(admin, model_id)
    cancelled = await admin.post(f'/api/runs/{original["id"]}/cancel', json={})
    assert cancelled.status_code == 200
    question = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()[0]
    regenerated = await admin.post(
        f'/api/chat/conversations/{conversation_id}/messages/{question["id"]}/regenerate',
        json={'client_request_id': str(uuid4())},
    )
    assert regenerated.status_code == 202, regenerated.text
    run = regenerated.json()
    async with container.database.sessions.begin() as session:
        claim = await container.runs.claim(session, UUID(run['id']), 60)
        assert claim is not None
        if provider_started:
            assert await container.runs.start_provider(session, claim.id, claim.attempt_id)
        assert await container.runs.project(session, claim.id, claim.attempt_id, '部分回答', '部分推理')
        await session.execute(
            text("UPDATE runtime_runs SET lease_until=now()-interval '1 second' WHERE id=:id"), {'id': claim.id}
        )
    await container.run_recovery.recover_expired()
    await container.run_recovery.recover_expired()
    snapshot = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert snapshot['status'] == 'interrupted' and snapshot['text'] == '部分回答'
    assert snapshot['usage']['status'] == ('unknown' if provider_started else 'known')
    usage = (await admin.get('/api/usage')).json()
    assert (usage['reserved_tokens'] > 0) is provider_started
    assert usage['used_tokens'] == 0
    async with container.database.sessions() as session:
        reply = await session.scalar(
            select(ChatMessage).where(
                ChatMessage.conversation_id == UUID(conversation_id), ChatMessage.role == 'assistant'
            )
        )
        assert reply is not None and reply.content == '部分回答' and not reply.is_complete
        assert not await container.runs.project(session, claim.id, claim.attempt_id, '过期输出')
