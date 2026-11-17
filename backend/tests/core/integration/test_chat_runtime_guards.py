"""Business-history and parent-attempt guards for the formal ordinary-chat runtime."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from tests.core.chat_support import admit, conversation, memory_head, wire_messages
from tests.core.provider import ProviderHandler
from zhiheng.modules.billing.orm import Invocation
from zhiheng.modules.chat.context_orm import SummaryTask
from zhiheng.modules.models.public import ModelMessage, ModelRequest
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.worker.execution import execute_run

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests.core.provider import ProviderServer
    from zhiheng.bootstrap.container import Container
    from zhiheng.runtime.contracts import ClaimedRun


async def claimed_parent(admin: httpx.AsyncClient, app: FastAPI, model_id: str) -> ClaimedRun:
    cid = await conversation(admin)
    snapshot = await admit(admin, cid, model_id, '当前父回复')
    async with app.state.container.database.sessions.begin() as session:
        parent = await app.state.container.runs.claim(session, UUID(snapshot['id']), 30)
    assert parent is not None
    return parent


async def invalidate_parent(container: Container, parent: ClaimedRun, reason: str) -> None:
    async with container.database.sessions.begin() as session:
        if reason == 'cancel_requested':
            snapshot, release = await container.runs.cancel(session, parent.id, parent.owner_id)
            assert snapshot.status == 'cancel_requested' and not release
        else:
            row = await session.get(Run, parent.id)
            assert row is not None
            row.attempt_id = uuid4()


async def test_empty_stop_reply_is_not_effective_history_and_followup_can_continue(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ProviderHandler.do_POST
    first = True

    def empty_once(handler: ProviderHandler) -> None:
        nonlocal first
        if not first:
            original(handler)
            return
        first = False
        body = json.loads(handler.rfile.read(int(handler.headers['Content-Length'])))
        handler.server.requests.append(body)
        handler.server.request_headers.append({key.lower(): value for key, value in handler.headers.items()})
        handler.send_response(200)
        handler.send_header('Content-Type', 'text/event-stream')
        handler.end_headers()
        chunk = {
            'id': 'empty-reply',
            'object': 'chat.completion.chunk',
            'created': 1,
            'model': body['model'],
            'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 8, 'completion_tokens': 0, 'total_tokens': 8},
        }
        handler.wfile.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
        handler.wfile.flush()

    monkeypatch.setattr(ProviderHandler, 'do_POST', empty_once)
    cid = await conversation(admin)
    empty = await admit(admin, cid, model_id, '不应进入有效历史的空回答问题')
    await execute_run(app.state.container, UUID(empty['id']))
    result = (await admin.get(f'/api/runs/{empty["id"]}')).json()
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'chat_empty_response'
    assert result['text'] == '' and result['finish_reason'] == 'stop'
    assert result['usage'] == {'input_tokens': 8, 'output_tokens': 0, 'status': 'known'}
    assert await memory_head(app, cid) is None
    container = app.state.container
    actor = (await admin.get('/api/auth/me')).json()
    async with container.database.sessions() as session:
        principal = await container.identity.principal_for_user(session, UUID(actor['id']))
        assert await container.chat.context_turns(session, principal, UUID(cid)) == ()

    next_run = await admit(admin, cid, model_id, '空回答之后的新问题')
    await execute_run(container, UUID(next_run['id']))
    next_result = (await admin.get(f'/api/runs/{next_run["id"]}')).json()
    assert next_result['status'] == 'completed' and next_result['text'] == '你好'
    assert len(provider.requests) == 2
    assert wire_messages(provider) == [('user', '空回答之后的新问题')]
    assert await memory_head(app, cid) is not None


@pytest.mark.parametrize('reason', ['cancel_requested', 'attempt_changed'])
async def test_invalid_parent_cannot_admit_summary_child(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, reason: str
) -> None:
    container = app.state.container
    parent = await claimed_parent(admin, app, model_id)
    await invalidate_parent(container, parent, reason)
    binding = ModelRequest.model_validate(parent.payload).binding
    with pytest.raises(AppError) as lost:
        await container.chat_agent.summarize(
            parent, binding, (ModelMessage('system', '摘要规则'), ModelMessage('user', '待整理历史'))
        )
    assert lost.value.code == 'execution_lost'
    async with container.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(SummaryTask).where(SummaryTask.parent_run_id == parent.id)
            )
            == 0
        )
        assert (
            await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'chat_summary')) == 0
        )
        stored = await session.get(Run, parent.id)
        assert stored is not None and not stored.provider_started
    assert provider.requests == []


@pytest.mark.parametrize('reason', ['cancel_requested', 'attempt_changed'])
async def test_admitted_summary_cannot_start_provider_after_parent_becomes_invalid(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer, reason: str
) -> None:
    container = app.state.container
    parent = await claimed_parent(admin, app, model_id)
    binding = ModelRequest.model_validate(parent.payload).binding
    summary = asyncio.create_task(
        container.chat_agent.summarize(
            parent, binding, (ModelMessage('system', '摘要规则'), ModelMessage('user', '待整理历史'))
        )
    )
    try:
        child_id = None
        async with asyncio.timeout(3):
            while child_id is None:
                async with container.database.sessions() as session:
                    child_id = await session.scalar(
                        select(SummaryTask.run_id).where(SummaryTask.parent_run_id == parent.id)
                    )
                if child_id is None:
                    await asyncio.sleep(0.01)
        await invalidate_parent(container, parent, reason)
        await execute_run(container, child_id)
        with pytest.raises(AppError) as failed:
            await asyncio.wait_for(summary, 3)
        assert failed.value.code == 'chat_summary_failed'
        async with container.database.sessions() as session:
            child = await session.get(Run, child_id)
            invocation = await session.scalar(select(Invocation).where(Invocation.run_id == child_id))
            task = await session.scalar(select(SummaryTask).where(SummaryTask.parent_run_id == parent.id))
            assert child is not None and child.status == 'failed' and not child.provider_started
            assert child.input_tokens == child.output_tokens == 0
            assert invocation is not None and invocation.status == 'known' and invocation.charged_tokens == 0
            assert task is not None and task.status == 'failed'
        assert provider.requests == []
    finally:
        if not summary.done():
            summary.cancel()
        await asyncio.gather(summary, return_exceptions=True)
