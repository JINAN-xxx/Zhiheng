"""Ordinary chat uses real Agent/checkpoint execution and local provider HTTP."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from io import BytesIO
from threading import Event
from time import monotonic, sleep
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from tests.core.chat_support import (
    admit,
    completed,
    conversation,
    execute_with_summaries,
    frozen_context,
    memory_head,
    wire_messages,
)
from tests.core.integration.test_billing_runtime import prepare as prepare_points
from tests.core.provider import ProviderHandler
from zhiheng.bootstrap.container import Container
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.agents.adapters.checkpoints import CheckpointStore
from zhiheng.modules.billing.orm import CreditHold, CreditLedger, Invocation
from zhiheng.modules.chat.context_orm import SummaryTask
from zhiheng.modules.models.public import ModelRequest
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests.core.provider import ProviderServer


@pytest.fixture
def settings(settings: Settings, request: pytest.FixtureRequest) -> Settings:
    """Only this module opts into Agent execution; legacy integration stays unchanged."""
    return Settings.model_validate(
        {
            **settings.model_dump(),
            'chat_summary_trigger_tokens': 8192,
            'chat_summary_keep_tokens': 1024,
            'provider_timeout_seconds': 5,
            **getattr(request, 'param', {}),
        }
    )


async def checkpoint_parents(app: FastAPI, conversation_id: str) -> dict[str, str | None]:
    async with app.state.container.database.sessions() as session:
        rows = await session.execute(
            text(
                'SELECT checkpoint_id, parent_checkpoint_id FROM chat_checkpoints.checkpoints '
                'WHERE thread_id=:thread AND checkpoint_ns=:namespace'
            ),
            {'thread': conversation_id, 'namespace': ''},
        )
        return {row.checkpoint_id: row.parent_checkpoint_id for row in rows}


def ancestors(parents: dict[str, str | None], checkpoint_id: str) -> set[str]:
    found: set[str] = set()
    current: str | None = checkpoint_id
    while current is not None and current in parents and current not in found:
        found.add(current)
        current = parents[current]
    return found


async def test_agent_chat_calls_once_across_admission_replay_delivery_and_snapshot_reconnect(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer
) -> None:
    cid, key = await conversation(admin), str(uuid4())
    run = await admit(admin, cid, model_id, '仅执行这一次', request_id=key)
    replay = await admit(admin, cid, model_id, '仅执行这一次', request_id=key)
    assert replay['id'] == run['id']
    await asyncio.gather(
        execute_run(app.state.container, UUID(run['id'])), execute_run(app.state.container, UUID(run['id']))
    )
    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == 'completed' and result['text'] == '你好', result
    assert result['usage'] == {'input_tokens': 8, 'output_tokens': 2, 'status': 'known'}
    for _ in range(2):
        events = await admin.get(f'/api/runs/{run["id"]}/events')
        assert events.status_code == 200 and 'event: snapshot' in events.text and 'completed' in events.text
    assert (await admit(admin, cid, model_id, '仅执行这一次', request_id=key))['id'] == run['id']
    assert len(provider.requests) == 1
    history = (await admin.get(f'/api/chat/conversations/{cid}/messages')).json()
    assert [message['role'] for message in history] == ['user', 'assistant']
    snapshot = await frozen_context(app, run['id'])
    head = await memory_head(app, cid)
    assert head is not None and snapshot.agent_checkpoint_id == head
    assert snapshot.agent_context['incremental'] is False
    usage = (await admin.get('/api/usage')).json()
    assert len(usage['records']) == 1 and usage['used_tokens'] == 10 and usage['reserved_tokens'] == 0


async def test_followup_restores_postgres_checkpoint_in_a_fresh_worker_without_history_duplicates(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer, settings: Settings
) -> None:
    await prepare_points(admin, app, model_id)
    cid = await conversation(admin)
    first = await admit(admin, cid, model_id, '第一轮')
    await completed(admin, app, first['id'])
    first_head = await memory_head(app, cid)
    assert first_head is not None
    store = CheckpointStore(settings.database_url.get_secret_value())
    async with store.saver() as saver:
        durable = await saver.aget_tuple(store.config(UUID(cid), first_head))
        assert durable is not None and durable.config['configurable']['checkpoint_id'] == first_head
    second = await admit(admin, cid, model_id, '第二轮')
    frozen = await frozen_context(app, second['id'])
    assert frozen.agent_context['incremental'] is True
    assert frozen.agent_context['base_checkpoint_id'] == first_head
    async with app.state.container.database.sessions() as session:
        payload = ModelRequest.model_validate((await session.get(Run, UUID(second['id']))).payload)
    assert [message.content for message in payload.messages if message.role != 'system'] == ['第二轮']
    minimum_context = sum(len(content.encode()) + 16 for content in ('第一轮', '你好', '第二轮'))
    previous = await frozen_context(app, first['id'])
    assert previous.agent_context['chat_context_reservation_tokens'] >= minimum_context - len('第二轮'.encode()) - 16
    assert frozen.agent_context['reservation_input_tokens'] >= minimum_context
    async with app.state.container.database.sessions() as session:
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == UUID(second['id'])))
        hold = await session.get(CreditHold, UUID(second['id']))
        assert invocation is not None and hold is not None
        assert invocation.reserved_tokens >= minimum_context + payload.binding.output_reservation_tokens
        minimum_points = (
            Decimal(minimum_context) * 100 + Decimal(payload.binding.output_reservation_tokens) * 500
        ) / 1_000_000
        assert hold.status == 'reserved' and hold.reserved_points >= minimum_points
    fresh_worker = Container(settings)
    try:
        await execute_run(fresh_worker, UUID(second['id']))
    finally:
        await fresh_worker.close()
    result = (await admin.get(f'/api/runs/{second["id"]}')).json()
    assert result['status'] == 'completed', result
    assert len(provider.requests) == 2
    assert wire_messages(provider) == [('user', '第一轮'), ('assistant', '你好'), ('user', '第二轮')]
    second_head = await memory_head(app, cid)
    assert second_head is not None and second_head != first_head
    parents = await checkpoint_parents(app, cid)
    assert first_head in ancestors(parents, second_head)
    assert (await frozen_context(app, second['id'])).agent_checkpoint_id == second_head


async def test_regeneration_publishes_a_branch_and_next_turn_uses_only_the_latest_reply(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer
) -> None:
    cid = await conversation(admin)
    original = await admit(admin, cid, model_id, '原问题')
    await completed(admin, app, original['id'])
    original_head = await memory_head(app, cid)
    history = (await admin.get(f'/api/chat/conversations/{cid}/messages')).json()
    endpoint = f'/api/chat/conversations/{cid}/messages/{history[-1]["id"]}/regenerate'
    key = str(uuid4())
    accepted = await admin.post(endpoint, json={'client_request_id': key})
    assert accepted.status_code == 202, accepted.text
    regenerated = accepted.json()
    assert regenerated['id'] != original['id']
    assert (await admin.post(endpoint, json={'client_request_id': key})).json()['id'] == regenerated['id']
    await completed(admin, app, regenerated['id'])
    branch_head = await memory_head(app, cid)
    assert original_head is not None and branch_head is not None and branch_head != original_head
    parents = await checkpoint_parents(app, cid)
    assert original_head not in ancestors(parents, branch_head)
    versions = (await admin.get(f'/api/chat/conversations/{cid}/messages')).json()
    assert [message['role'] for message in versions] == ['user', 'assistant', 'assistant']
    assert [message['generation_index'] for message in versions[1:]] == [1, 2]
    assert versions[1]['id'] == history[1]['id']
    followup = await admit(admin, cid, model_id, '继续')
    assert (await frozen_context(app, followup['id'])).agent_context['base_checkpoint_id'] == branch_head
    await completed(admin, app, followup['id'])
    assert len(provider.requests) == 3
    assert wire_messages(provider) == [('user', '原问题'), ('assistant', '你好'), ('user', '继续')]
    final_head = await memory_head(app, cid)
    assert final_head is not None
    parents = await checkpoint_parents(app, cid)
    assert branch_head in ancestors(parents, final_head)
    assert original_head not in ancestors(parents, final_head)


@pytest.mark.parametrize(
    'settings', [{'chat_summary_trigger_tokens': 256, 'chat_summary_keep_tokens': 64}], indirect=True
)
async def test_token_summary_uses_an_independent_native_run_and_settles_its_own_charge_once(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer
) -> None:
    await prepare_points(admin, app, model_id)
    provider.chat_summary_output = '已记住代号蓝鲸。'
    cid = await conversation(admin)
    first = await admit(admin, cid, model_id, '第一轮：记住代号蓝鲸。')
    await completed(admin, app, first['id'])
    second = await admit(admin, cid, model_id, '第二轮：继续处理。\n' + 'supporting material ' * 100)
    await execute_with_summaries(app, second['id'])
    result = (await admin.get(f'/api/runs/{second["id"]}')).json()
    assert result['status'] == 'completed' and result['text'] == '你好', result
    async with app.state.container.database.sessions() as session:
        summaries = list(await session.scalars(select(Run).where(Run.resource_type == 'chat_summary')))
        assert len(summaries) == 1
        summary = summaries[0]
        assert summary.resource_id != UUID(cid) and summary.status == 'completed'
        task = await session.get(SummaryTask, summary.resource_id)
        assert task is not None and task.status == 'published' and task.summary == provider.chat_summary_output
        hold = await session.get(CreditHold, summary.id)
        assert hold is not None and hold.source == 'chat_summary' and hold.status == 'settled'
        assert hold.charged_points == Decimal('0.0018')
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == summary.id))
        assert invocation is not None and invocation.source == 'chat_summary' and invocation.status == 'known'
        assert (invocation.input_tokens, invocation.output_tokens, invocation.charged_tokens) == (8, 2, 10)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CreditLedger)
                .where(CreditLedger.run_id == summary.id, CreditLedger.kind == 'settle')
            )
            == 1
        )
        summary_id = summary.id
    await execute_run(app.state.container, summary_id)
    assert len(provider.requests) == 3
    assert any('蓝鲸' in content for _role, content in wire_messages(provider))
    usage = (await admin.get('/api/usage')).json()
    assert len(usage['records']) == 3 and usage['used_tokens'] == 30 and usage['reserved_tokens'] == 0
    assert len([row for row in usage['records'] if row['source'] == 'chat_summary']) == 1
    info = (await admin.get(f'/api/chat/conversations/{cid}/context')).json()
    assert info['strategy'] == 'token' and info['effective_memory']['summary_used']
    assert info['effective_memory']['covered_rounds'] + info['effective_memory']['retained_rounds'] == 2
    assert (
        info['last_run_context']['summary_trigger_tokens'] == 256
        and info['last_run_context']['retained_token_budget'] == 64
    )
    history = (await admin.get(f'/api/chat/conversations/{cid}/messages')).json()
    assert [message['role'] for message in history] == ['user', 'assistant', 'user', 'assistant']


@pytest.mark.parametrize(
    'settings',
    [{'chat_summary_trigger_tokens': 256, 'chat_summary_keep_tokens': 64, 'provider_timeout_seconds': 2}],
    indirect=True,
)
async def test_summary_and_main_each_have_their_own_provider_timeout(
    admin: httpx.AsyncClient,
    model_id: str,
    app: FastAPI,
    provider: ProviderServer,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = await conversation(admin)
    first = await admit(admin, cid, model_id, '第一轮：记住代号蓝鲸。')
    await completed(admin, app, first['id'])
    provider.chat_summary_output = '已记住代号蓝鲸。'
    original = ProviderHandler.do_POST
    durations: list[float] = []
    finished = Event()

    def delayed(handler: ProviderHandler) -> None:
        started = monotonic()
        sleep(1.2)
        try:
            original(handler)
        finally:
            durations.append(monotonic() - started)
            if len(durations) == 2:
                finished.set()

    monkeypatch.setattr(ProviderHandler, 'do_POST', delayed)
    second = await admit(admin, cid, model_id, '第二轮：继续处理。\n' + 'supporting material ' * 100)
    started = monotonic()
    await execute_with_summaries(app, second['id'])
    elapsed = monotonic() - started
    assert await asyncio.to_thread(finished.wait, 1)
    result = (await admin.get(f'/api/runs/{second["id"]}')).json()
    assert result['status'] == 'completed' and result['text'] == '你好', result
    assert len(provider.requests) == 3 and len(durations) == 2
    assert all(duration < settings.provider_timeout_seconds for duration in durations), durations
    assert elapsed > settings.provider_timeout_seconds, elapsed


@pytest.mark.parametrize('stage', ['pending', 'running'])
async def test_cancelled_agent_branch_cannot_become_the_next_turn_history(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer, stage: str
) -> None:
    cid = await conversation(admin)
    if stage == 'running':
        assert (await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'silent'})).status_code == 200
    run = await admit(admin, cid, model_id, '不应进入下一轮的已取消问题')
    execution = asyncio.create_task(execute_run(app.state.container, UUID(run['id']))) if stage == 'running' else None
    try:
        if execution is not None:
            async with asyncio.timeout(3):
                while not provider.requests:  # noqa: ASYNC110 - the local HTTP fixture records requests from its server thread
                    await asyncio.sleep(0.01)
        cancelled = await admin.post(f'/api/runs/{run["id"]}/cancel', json={})
        assert cancelled.status_code == 200, cancelled.text
        provider.release.set()
        if execution is not None:
            await asyncio.wait_for(execution, timeout=6)
        else:
            await execute_run(app.state.container, UUID(run['id']))
        result = (await admin.get(f'/api/runs/{run["id"]}')).json()
        assert result['status'] == 'cancelled', result
        assert await memory_head(app, cid) is None
        before = len(provider.requests)
        assert before == (1 if stage == 'running' else 0)
        if stage == 'running':
            assert (
                await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'local-text'})
            ).status_code == 200
        followup = await admit(admin, cid, model_id, '取消之后的新问题')
        assert (await frozen_context(app, followup['id'])).agent_context['base_checkpoint_id'] is None
        await completed(admin, app, followup['id'])
        assert len(provider.requests) == before + 1
        assert wire_messages(provider) == [('user', '取消之后的新问题')]
    finally:
        provider.release.set()
        if execution is not None and not execution.done():
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)


async def test_formal_chat_rejects_knowledge_selection_without_creating_paid_work(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    cid = await conversation(admin)
    response = await admin.post(
        f'/api/chat/conversations/{cid}/runs',
        json={
            'model_id': model_id,
            'message': 'plain chat',
            'client_request_id': str(uuid4()),
            'knowledge_base_ids': [str(uuid4())],
        },
    )
    assert response.status_code == 422
    assert not provider.requests
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Run)) == 0


@pytest.mark.parametrize(
    'settings', [{'chat_summary_trigger_tokens': 256, 'chat_summary_keep_tokens': 64}], indirect=True
)
async def test_regenerating_a_successful_retry_reuses_that_replies_prepared_summary(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ProviderHandler.do_POST
    reject_main_once = True

    def fail_second_main(handler: ProviderHandler) -> None:
        nonlocal reject_main_once
        raw = handler.rfile.read(int(handler.headers['Content-Length']))
        body = json.loads(raw)
        is_main = 'max_completion_tokens' not in body and 'max_tokens' not in body
        target = any('question to regenerate' in str(message['content']) for message in body['messages'])
        if reject_main_once and is_main and target:
            reject_main_once = False
            handler.server.requests.append(body)
            handler.server.request_headers.append(dict(handler.headers.items()))
            handler.send_response(503)
            handler.send_header('Content-Type', 'application/json')
            handler.end_headers()
            handler.wfile.write(b'{}')
            return
        handler.rfile = BytesIO(raw)
        original(handler)

    monkeypatch.setattr(ProviderHandler, 'do_POST', fail_second_main)
    cid = await conversation(admin)
    first = await admit(admin, cid, model_id, 'older context ' * 80)
    await completed(admin, app, first['id'])
    accepted_memory = (await admin.get(f'/api/chat/conversations/{cid}/context')).json()['effective_memory']
    second = await admit(admin, cid, model_id, 'question to regenerate')
    await execute_with_summaries(app, second['id'])
    assert (await admin.get(f'/api/runs/{second["id"]}')).json()['status'] == 'failed'
    projection = (await admin.get(f'/api/chat/conversations/{cid}/context')).json()
    assert projection['status'] == 'full'
    assert projection['effective_memory'] == accepted_memory
    assert projection['last_run_context']['run_id'] == second['id']
    assert projection['last_run_context']['summary_prepared'] is True
    assert projection['last_run_context']['estimated_input_tokens'] is None
    messages = (await admin.get(f'/api/chat/conversations/{cid}/messages')).json()
    current_question = next(row for row in messages if row['run_id'] == second['id'] and row['role'] == 'user')
    retried = await admin.post(
        f'/api/chat/conversations/{cid}/messages/{current_question["id"]}/regenerate',
        json={'client_request_id': str(uuid4())},
    )
    assert retried.status_code == 202, retried.text
    await execute_with_summaries(app, retried.json()['id'])
    assert (await admin.get(f'/api/runs/{retried.json()["id"]}')).json()['status'] == 'completed'
    before = len(provider.requests)
    messages = (await admin.get(f'/api/chat/conversations/{cid}/messages')).json()
    successful_reply = next(
        row for row in messages if row['run_id'] == retried.json()['id'] and row['role'] == 'assistant'
    )
    regenerated = await admin.post(
        f'/api/chat/conversations/{cid}/messages/{successful_reply["id"]}/regenerate',
        json={'client_request_id': str(uuid4())},
    )
    assert regenerated.status_code == 202, regenerated.text
    await execute_with_summaries(app, regenerated.json()['id'])
    result = (await admin.get(f'/api/runs/{regenerated.json()["id"]}')).json()
    assert result['status'] == 'completed', result
    assert len(provider.requests) == before + 1


@pytest.mark.parametrize(
    'settings',
    [
        {
            'chat_context_input_tokens': 256,
            'chat_summary_trigger_tokens': 256,
            'chat_summary_keep_tokens': 64,
            'chat_summary_output_tokens': 64,
        }
    ],
    indirect=True,
)
async def test_summary_capacity_rejection_creates_no_paid_summary_child(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    await prepare_points(admin, app, model_id)
    cid = await conversation(admin)
    first = await admit(admin, cid, model_id, 'older ' * 35)
    await completed(admin, app, first['id'])
    head = await memory_head(app, cid)
    accepted = await admin.post(
        f'/api/chat/conversations/{cid}/runs',
        json={'model_id': model_id, 'message': 'next', 'system_prompt': '规则' * 43, 'client_request_id': str(uuid4())},
    )
    assert accepted.status_code == 202, accepted.text
    await execute_with_summaries(app, accepted.json()['id'])
    result = (await admin.get(f'/api/runs/{accepted.json()["id"]}')).json()
    assert result['status'] == 'failed' and result['error']['code'] == 'context_input_too_long', result
    assert len(provider.requests) == 1
    assert await memory_head(app, cid) == head
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(SummaryTask)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'chat_summary')) == 0
        )
        hold = await session.get(CreditHold, UUID(accepted.json()['id']))
        assert hold is not None and hold.status == 'settled' and hold.charged_points == 0


@pytest.mark.parametrize('settings', [{'provider_timeout_seconds': 2}], indirect=True)
@pytest.mark.parametrize('interruption', ['cancel', 'timeout'])
async def test_known_usage_survives_interruption_while_checkpoint_io_is_pending(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    interruption: str,
) -> None:
    from typing import Any

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    entered = asyncio.Event()
    original = AsyncPostgresSaver.alist

    async def paused_result_read(saver: AsyncPostgresSaver, *args: Any, **kwargs: Any) -> Any:
        if kwargs.get('filter', {}).get('agent_execution_id'):
            entered.set()
            await asyncio.Event().wait()
        async for checkpoint in original(saver, *args, **kwargs):
            yield checkpoint

    await prepare_points(admin, app, model_id)
    cid = await conversation(admin)
    run = await admit(admin, cid, model_id, 'The response and exact usage arrive before checkpoint IO completes.')
    assert await memory_head(app, cid) is None
    monkeypatch.setattr(AsyncPostgresSaver, 'alist', paused_result_read)
    execution = asyncio.create_task(execute_run(app.state.container, UUID(run['id'])))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert len(provider.requests) == 1
        if interruption == 'cancel':
            cancelled = await admin.post(f'/api/runs/{run["id"]}/cancel', json={})
            assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancel_requested', cancelled.text
        await asyncio.wait_for(execution, timeout=5)
    finally:
        if not execution.done():
            execution.cancel()
        await asyncio.gather(execution, return_exceptions=True)

    result = (await admin.get(f'/api/runs/{run["id"]}')).json()
    assert result['status'] == ('cancelled' if interruption == 'cancel' else 'failed'), result
    if interruption == 'timeout':
        assert result['error']['code'] == 'model_finalize_timeout'
    assert result['text'] == '你好' and result['finish_reason'] == 'stop'
    assert result['usage'] == {'input_tokens': 8, 'output_tokens': 2, 'status': 'known'}
    assert await memory_head(app, cid) is None
    assert len(provider.requests) == 1
    frozen = await frozen_context(app, run['id'])
    assert frozen.agent_checkpoint_id is None
    async with app.state.container.database.sessions() as session:
        native = await session.get(Run, UUID(run['id']))
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == native.id))
        hold = await session.get(CreditHold, native.id)
        assert native.provider_started and native.input_tokens == 8 and native.output_tokens == 2
        assert invocation is not None and invocation.status == 'known'
        assert (invocation.input_tokens, invocation.output_tokens, invocation.charged_tokens) == (8, 2, 10)
        assert hold is not None and hold.status == 'settled' and hold.charged_points == Decimal('0.0018')
        assert hold.usage_snapshot['input_tokens'] == 8 and hold.usage_snapshot['output_tokens'] == 2
        entries = list(await session.scalars(select(CreditLedger).where(CreditLedger.run_id == native.id)))
        assert [entry.kind for entry in entries].count('settle') == 1
        assert not any(entry.kind in {'unknown', 'failure_waive'} for entry in entries)
