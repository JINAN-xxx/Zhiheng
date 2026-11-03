"""Conversation summaries use real SQL transactions and the native local inference path."""

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, func, select, text

from tests.core.integration.test_billing_runtime import prepare as prepare_points
from tests.core.integration.test_skills import install as install_skill
from tests.core.provider import ProviderServer
from zhiheng.application.inference import InferenceExecutor
from zhiheng.modules.billing.orm import CreditHold, CreditLedger
from zhiheng.modules.chat.context_orm import ContextState, SummaryTask
from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.modules.developer.orm import Invocation
from zhiheng.modules.files.orm import FileRecord
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.modules.models.public import ModelRequest
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow
from zhiheng.worker.execution import execute_run

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def seed_history(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    count: int = 29,
    reconcile: bool = True,
    history_padding: str = '',
    system_prompt: str = '',
) -> tuple[UUID, dict[str, object], UUID]:
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    container = app.state.container
    async with container.database.sessions.begin() as session:
        owner = await session.scalar(select(Conversation.owner_id).where(Conversation.id == UUID(conversation['id'])))
        binding = await container.models.resolve(session, UUID(model_id))
        for index in range(count):
            run_id, question_id = uuid4(), uuid4()
            created = utcnow() - timedelta(minutes=count - index)
            session.add(
                Run(
                    id=run_id,
                    owner_id=owner,
                    billing_owner_id=owner,
                    resource_type='chat',
                    resource_id=UUID(conversation['id']),
                    client_request_id=uuid4(),
                    fingerprint=f'{index:064x}',
                    payload=ModelRequest(binding=binding, messages=()).model_dump(mode='json'),
                    status='completed',
                    finish_reason='stop',
                    text=f'回答 {index + 1}',
                    created_at=created,
                )
            )
            await session.flush()
            session.add(
                ChatMessage(
                    id=question_id,
                    conversation_id=UUID(conversation['id']),
                    run_id=run_id,
                    role='user',
                    content=f'问题 {index + 1}' + history_padding,
                    is_complete=True,
                    created_at=created,
                )
            )
            await session.flush()
            session.add(
                ChatMessage(
                    conversation_id=UUID(conversation['id']),
                    run_id=run_id,
                    role='assistant',
                    reply_to_message_id=question_id,
                    content=f'回答 {index + 1}',
                    reasoning='历史推理不可进入摘要',
                    is_complete=True,
                    created_at=created + timedelta(seconds=1),
                )
            )
    # An ordinary request initializes the durable state, just as an upgraded conversation does.
    result = await admin.post(
        f'/api/chat/conversations/{conversation["id"]}/runs',
        json={
            'model_id': model_id,
            'message': f'第{count + 1}个问题',
            'system_prompt': system_prompt,
            'client_request_id': str(uuid4()),
        },
    )
    assert result.status_code == 202, result.text
    await execute_native(app, UUID(result.json()['id']), reconcile=reconcile)
    return UUID(conversation['id']), result.json(), owner


async def execute_native(app: FastAPI, run_id: UUID, *, reconcile: bool = False) -> None:
    container = app.state.container
    if reconcile:
        await execute_run(container, run_id)
        return
    executor = InferenceExecutor(
        sessions=container.database.sessions,
        runs=container.runs,
        usage=container.usage,
        chat=container.chat,
        provider_factory=container.prepare_provider,
        lease_seconds=container.settings.lease_seconds,
        timeout_seconds=container.settings.provider_timeout_seconds,
        before_provider=container.require_model_execution,
    )
    await executor.execute(run_id)


async def summary_run(app: FastAPI) -> Run | None:
    async with app.state.container.database.sessions() as session:
        return await session.scalar(
            select(Run).where(Run.resource_type == 'chat_summary').order_by(Run.created_at.desc())
        )


async def test_summary_worker_charges_once_preserves_history_and_next_prompt(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    conversation_id, _main, _ = await seed_history(admin, app, model_id)
    container = app.state.container
    await container.chat_context.reconcile()
    task_run = await summary_run(app)
    assert task_run is not None and task_run.resource_id != conversation_id
    await asyncio.gather(execute_run(container, task_run.id), execute_run(container, task_run.id))
    await container.chat_context.reconcile()
    await container.chat_context.reconcile()
    async with container.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
            )
            == 60
        )
        assert (
            await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'chat_summary')) == 1
        )
    assert len(provider.requests) == 2
    summary_prompt = str(provider.requests[-1]['messages'])
    assert '问题 1' in summary_prompt and '问题 15' in summary_prompt
    assert '历史推理不可进入摘要' not in summary_prompt
    next_request = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': '下一轮',
            'client_request_id': str(uuid4()),
        },
    )
    assert next_request.status_code == 202, next_request.text
    async with container.database.sessions() as session:
        run = await session.get(Run, UUID(next_request.json()['id']))
        messages = ModelRequest.model_validate(run.payload).messages
        assert any('会话摘要' in message.content and '你好' in message.content for message in messages)
        assert any(message.content == '问题 16' for message in messages)
        assert all(message.content != '问题 1' for message in messages)
    usage = (await admin.get('/api/usage')).json()['records']
    assert len([item for item in usage if item['source'] == 'chat_summary']) == 1


async def test_summary_failure_preserves_old_memory_and_does_not_retry_same_source(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id)
    container = app.state.container
    await container.chat_context.reconcile()
    task_run = await summary_run(app)
    assert task_run is not None
    # Change only this frozen test request, without modifying global/default provider behavior.
    async with container.database.sessions.begin() as session:
        row = await session.get(Run, task_run.id)
        payload = dict(row.payload)
        payload['binding'] = {**payload['binding'], 'upstream_model': 'truncated'}
        row.payload = payload
    await execute_run(container, task_run.id)
    await container.chat_context.reconcile()
    await container.chat_context.reconcile()
    async with container.database.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'chat_summary')) == 1
        )
    result = await admin.get(f'/api/chat/conversations/{conversation_id}/context')
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'degraded'
    request = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': '失败后仍可继续',
            'client_request_id': str(uuid4()),
        },
    )
    assert request.status_code == 202, request.text


async def test_archived_summary_is_cancelled_before_provider_and_releases_reservation(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id)
    container = app.state.container
    await container.chat_context.reconcile()
    task_run = await summary_run(app)
    assert task_run is not None
    response = await admin.patch(
        f'/api/chat/conversations/{conversation_id}', json={'expected_version': 1, 'archived': True}
    )
    assert response.status_code == 200, response.text
    await execute_run(container, task_run.id)
    await container.chat_context.reconcile()
    assert len(provider.requests) == 1
    async with container.database.sessions() as session:
        row = await session.get(Run, task_run.id)
        assert row.status in {'cancelled', 'failed'}
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] == 0


async def test_45_rounds_merge_two_contiguous_batches_with_old_summary(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id, count=44)
    first = await summary_run(app)
    assert first is not None
    await execute_run(app.state.container, first.id)
    second = await summary_run(app)
    assert second is not None and second.id != first.id
    await execute_run(app.state.container, second.id)
    info = (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()
    assert (info['summary_version'], info['covered_rounds'], info['retained_rounds']) == (2, 30, 15)
    prompt = str(provider.requests[-1]['messages'])
    assert '会话摘要' in prompt and '问题 16' in prompt and '问题 30' in prompt
    async with app.state.container.database.sessions() as session:
        tasks = list(await session.scalars(select(SummaryTask).order_by(SummaryTask.created_at)))
        assert [task.covered_count for task in tasks] == [15, 30]
        assert tasks[1].source_refs[:15] == tasks[0].source_refs
        assert tasks[1].previous_task_id == tasks[0].id


async def test_periodic_recovers_main_commit_and_summary_commit_before_callbacks(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id, reconcile=False)
    assert await summary_run(app) is None
    await app.state.container.chat_context.reconcile()
    run = await summary_run(app)
    assert run is not None
    await execute_native(app, run.id)
    async with app.state.container.database.sessions() as session:
        assert (await session.get(ContextState, conversation_id)).version == 0
    await app.state.container.chat_context.reconcile()
    async with app.state.container.database.sessions() as session:
        assert (await session.get(ContextState, conversation_id)).version == 1
    assert len(provider.requests) == 2


@pytest.mark.parametrize('provider_started', [False, True])
async def test_expired_summary_lease_is_settled_once_and_does_not_restart_paid_call(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    provider_started: bool,
) -> None:
    from zhiheng.worker.main import recover_expired

    conversation_id, _, _ = await seed_history(admin, app, model_id)
    container = app.state.container
    run = await summary_run(app)
    assert run is not None
    async with container.database.sessions.begin() as session:
        assert await container.runs.claim(session, run.id, 3)
        row = await session.get(Run, run.id)
        row.provider_started, row.lease_until = provider_started, utcnow() - timedelta(seconds=1)
    await recover_expired(container)
    await container.chat_context.reconcile()
    await execute_run(container, run.id)
    assert len(provider.requests) == 1
    async with container.database.sessions() as session:
        row = await session.get(Run, run.id)
        assert row.status == 'interrupted'
        assert row.usage_status == ('unknown' if provider_started else 'known')
        assert (
            await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'chat_summary')) == 1
        )
    assert (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()['status'] == 'degraded'


@pytest.mark.parametrize('kind', ['model', 'owner', 'source', 'deleted'])
async def test_pending_summary_rechecks_current_owner_model_and_source_before_spending(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    kind: str,
) -> None:
    conversation_id, _, owner_id = await seed_history(admin, app, model_id)
    container = app.state.container
    run = await summary_run(app)
    assert run is not None
    if kind == 'model':
        assert (await admin.post(f'/api/models/{model_id}/disable', json={})).status_code == 200
    else:
        async with container.database.sessions.begin() as session:
            if kind == 'owner':
                (await session.get(IdentityUser, owner_id)).is_active = False
            elif kind == 'deleted':
                (await session.get(Conversation, conversation_id)).deleted_at = utcnow()
            else:
                row = await session.scalar(
                    select(ChatMessage).where(
                        ChatMessage.conversation_id == conversation_id, ChatMessage.content == '回答 1'
                    )
                )
                row.content = '覆盖来源发生变化'
    await execute_run(container, run.id)
    assert len(provider.requests) == 1
    async with container.database.sessions() as session:
        assert (await session.get(ContextState, conversation_id)).version == 0
        assert (await session.get(Run, run.id)).status in {'failed', 'cancelled'}


async def test_summary_publication_cas_discards_completed_stale_task(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id)
    container = app.state.container
    run = await summary_run(app)
    assert run is not None
    await execute_native(app, run.id)
    async with container.database.sessions.begin() as session:
        state = await session.get(ContextState, conversation_id)
        state.version += 1
    await container.chat_context.reconcile()
    async with container.database.sessions() as session:
        state = await session.get(ContextState, conversation_id)
        assert state.version == 1 and state.summary_task_id is None
        assert state.error_code == 'chat_summary_stale'


async def test_summary_with_unknown_usage_publishes_without_fabricating_usage(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    provider.chat_summary_usage = False
    conversation_id, _, _ = await seed_history(admin, app, model_id)
    run = await summary_run(app)
    assert run is not None
    await execute_run(app.state.container, run.id)
    result = (await admin.get(f'/api/runs/{run.id}')).json()
    assert result['status'] == 'completed' and result['usage']['status'] == 'unknown'
    assert result['usage']['input_tokens'] is None and result['usage']['output_tokens'] is None
    assert (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()['summary_version'] == 1


@pytest.mark.parametrize(('output', 'finish'), [('', 'stop'), ('半份摘要', 'length'), ('部分摘要', None)])
async def test_empty_truncated_or_unterminated_summary_never_publishes(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    output: str,
    finish: str | None,
) -> None:
    provider.chat_summary_output, provider.chat_summary_finish = output, finish
    conversation_id, _, _ = await seed_history(admin, app, model_id)
    run = await summary_run(app)
    assert run is not None
    await execute_run(app.state.container, run.id)
    info = (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()
    assert info['summary_version'] == 0 and info['status'] == 'degraded'


async def test_summary_admission_insufficient_quota_rolls_back_run_and_ledger_and_keeps_chat_usable(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id, reconcile=False)
    container = app.state.container
    before = (await admin.get('/api/usage')).json()
    actor = await prepare_points(admin, app, model_id, points='0.1')
    async with container.database.sessions() as session:
        private_before = (
            (await container.credits.summary(session, actor.id)).model_dump(mode='json'),
            await session.scalar(select(func.count()).select_from(CreditHold)),
            await session.scalar(select(func.count()).select_from(CreditLedger)),
            await session.scalar(select(func.count()).select_from(Invocation)),
        )
    await container.chat_context.reconcile()
    await container.chat_context.reconcile()
    after = (await admin.get('/api/usage')).json()
    assert (after['used_tokens'], after['reserved_tokens'], len(after['records'])) == (
        before['used_tokens'],
        0,
        len(before['records']),
    )
    assert await summary_run(app) is None
    async with container.database.sessions() as session:
        private_after = (
            (await container.credits.summary(session, actor.id)).model_dump(mode='json'),
            await session.scalar(select(func.count()).select_from(CreditHold)),
            await session.scalar(select(func.count()).select_from(CreditLedger)),
            await session.scalar(select(func.count()).select_from(Invocation)),
        )
        assert private_after == private_before
    info = (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()
    assert info['status'] == 'degraded'
    container.chat_context.input_tokens = 256
    result = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': '摘要额度不足仍可聊天',
            'client_request_id': str(uuid4()),
        },
    )
    assert result.status_code == 202, result.text


@pytest.mark.parametrize('after_publish', [False, True])
async def test_summary_file_provenance_is_rechecked_before_execution_and_when_reused(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    after_publish: bool,
) -> None:
    conversation_id, _, owner_id = await seed_history(admin, app, model_id, reconcile=False)
    container = app.state.container
    uploaded = await admin.post(
        '/api/files?filename=memory-source.txt',
        content=b'Constraint A',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = UUID(uploaded.json()['id'])
    async with container.database.sessions() as session:
        principal = await container.identity.principal_for_user(session, owner_id)
    attachments = await container.file_inputs.prepare_many(principal, [file_id])
    async with container.database.sessions.begin() as session:
        question = await session.scalar(
            select(ChatMessage).where(ChatMessage.conversation_id == conversation_id, ChatMessage.content == '问题 1')
        )
        question.attachments = [item.summary.model_dump(mode='json') for item in attachments]
        question.attachment_inputs = [item.model_dump(mode='json') for item in attachments]
    await container.chat_context.reconcile()
    run = await summary_run(app)
    assert run is not None
    if after_publish:
        await execute_run(container, run.id)
    async with container.database.sessions.begin() as session:
        (await session.get(FileRecord, file_id)).expires_at = utcnow() - timedelta(seconds=1)
    if after_publish:
        result = await admin.post(
            f'/api/chat/conversations/{conversation_id}/runs',
            json={
                'model_id': model_id,
                'message': '不能绕过旧附件访问',
                'client_request_id': str(uuid4()),
            },
        )
        assert result.status_code in {403, 404, 409}, result.text
    else:
        await execute_run(container, run.id)
        assert len(provider.requests) == 1
        async with container.database.sessions() as session:
            assert (await session.get(ContextState, conversation_id)).summary_task_id is None


async def test_context_info_is_read_only_for_an_unused_conversation(admin: httpx.AsyncClient, app: FastAPI) -> None:
    conversation_id = (await admin.post('/api/chat/conversations', json={})).json()['id']
    assert (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).status_code == 200
    async with app.state.container.database.sessions() as session:
        assert await session.get(ContextState, UUID(conversation_id)) is None


async def test_token_pressure_schedules_summary_before_30_completed_rounds(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    app.state.container.chat_context.input_tokens = 1000
    conversation_id, _, _ = await seed_history(admin, app, model_id, count=10, history_padding='重要历史约束。' * 20)
    run = await summary_run(app)
    assert run is not None
    info = (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()
    assert info['status'] == 'summarizing' and info['truncated']


async def test_failed_summary_can_be_explicitly_retried_with_durable_request_id(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    provider.chat_summary_output, provider.chat_summary_finish = '不完整', 'length'
    conversation_id, _, _ = await seed_history(admin, app, model_id)
    first = await summary_run(app)
    assert first is not None
    await execute_run(app.state.container, first.id)
    provider.chat_summary_finish = 'stop'
    key = str(uuid4())
    endpoint = f'/api/chat/conversations/{conversation_id}/context/retry'
    response = await admin.post(endpoint, json={'client_request_id': key})
    assert response.status_code == 202, response.text
    second = await summary_run(app)
    assert second is not None and second.id != first.id
    assert (await admin.post(endpoint, json={'client_request_id': key})).status_code == 202
    assert (await summary_run(app)).id == second.id
    await execute_run(app.state.container, second.id)
    assert (await admin.post(endpoint, json={'client_request_id': key})).status_code == 202
    assert (await summary_run(app)).id == second.id
    assert (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()['status'] == 'summarized'


async def test_regeneration_rejects_frozen_original_prompt_when_current_model_window_shrinks(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    conversation_id = (await admin.post('/api/chat/conversations', json={})).json()['id']
    await admin.patch(f'/api/models/{model_id}', json={'context_window_tokens': 8192})
    response = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': '文' * 800,
            'client_request_id': str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    await execute_run(app.state.container, UUID(response.json()['id']))
    history = (await admin.get(f'/api/chat/conversations/{conversation_id}/messages')).json()
    await admin.patch(f'/api/models/{model_id}', json={'context_window_tokens': 512})
    regenerated = await admin.post(
        f'/api/chat/conversations/{conversation_id}/messages/{history[-1]["id"]}/regenerate',
        json={'client_request_id': str(uuid4())},
    )
    assert regenerated.status_code == 422, regenerated.text
    assert regenerated.json()['error']['code'] == 'context_input_too_long'


async def test_concurrent_archive_and_summary_reconciliation_finish_without_lock_inversion(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id)
    response, _ = await asyncio.wait_for(
        asyncio.gather(
            admin.patch(f'/api/chat/conversations/{conversation_id}', json={'expected_version': 1, 'archived': True}),
            app.state.container.chat_context.reconcile(),
        ),
        timeout=5,
    )
    assert response.status_code == 200, response.text
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] == 0


async def test_light_and_hydrated_sources_have_identical_fingerprint_without_loading_old_images(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    from zhiheng.modules.chat.public import source_fingerprint

    conversation_id, _, owner_id = await seed_history(admin, app, model_id)
    container = app.state.container
    async with container.database.sessions() as session:
        principal = await container.identity.principal_for_user(session, owner_id)
        queries: list[str] = []

        def capture(
            connection: object, cursor: object, statement: str, parameters: object, context: object, executemany: bool
        ) -> None:
            queries.append(statement)

        event.listen(container.database.engine.sync_engine, 'before_cursor_execute', capture)
        try:
            light = await container.chat.context_turns(session, principal, conversation_id, load_inputs=False)
        finally:
            event.remove(container.database.engine.sync_engine, 'before_cursor_execute', capture)
        assert queries and all(
            'chat_messages.attachment_inputs' not in query and 'chat_messages.reasoning' not in query
            for query in queries
        )
        heavy = await container.chat.hydrate_turns(session, principal, conversation_id, light[:2])
        assert source_fingerprint(light[:2]) == source_fingerprint(heavy)


async def test_retry_history_replay_does_not_replace_a_new_pending_retry_or_cross_conversations(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    provider.chat_summary_output, provider.chat_summary_finish = '半份摘要', 'length'
    conversation_id, _, _ = await seed_history(admin, app, model_id)
    run = await summary_run(app)
    assert run is not None
    await execute_run(app.state.container, run.id)
    endpoint = f'/api/chat/conversations/{conversation_id}/context/retry'
    first_key, next_key = str(uuid4()), str(uuid4())
    assert (await admin.post(endpoint, json={'client_request_id': first_key})).status_code == 202
    first_retry = await summary_run(app)
    await execute_run(app.state.container, first_retry.id)
    assert (await admin.post(endpoint, json={'client_request_id': next_key})).status_code == 202
    current = await summary_run(app)
    assert (await admin.post(endpoint, json={'client_request_id': first_key})).status_code == 202
    assert (await summary_run(app)).id == current.id
    assert (await admin.post(endpoint, json={'client_request_id': str(uuid4())})).status_code == 409
    other = (await admin.post('/api/chat/conversations', json={})).json()['id']
    response = await admin.post(f'/api/chat/conversations/{other}/context/retry', json={'client_request_id': first_key})
    assert response.status_code == 409, response.text


async def test_expired_file_in_unselected_raw_history_does_not_block_budgeted_chat(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    conversation_id, _, owner_id = await seed_history(admin, app, model_id, count=20, reconcile=False)
    container = app.state.container
    uploaded = await admin.post(
        '/api/files?filename=old-source.txt',
        content=b'old source',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = UUID(uploaded.json()['id'])
    async with container.database.sessions() as session:
        principal = await container.identity.principal_for_user(session, owner_id)
    attachments = await container.file_inputs.prepare_many(principal, [file_id])
    async with container.database.sessions.begin() as session:
        first = await session.scalar(
            select(ChatMessage).where(ChatMessage.conversation_id == conversation_id, ChatMessage.content == '问题 1')
        )
        first.attachments = [item.summary.model_dump(mode='json') for item in attachments]
        first.attachment_inputs = [item.model_dump(mode='json') for item in attachments]
        (await session.get(FileRecord, file_id)).expires_at = utcnow() - timedelta(seconds=1)
    container.chat_context.input_tokens = 300
    result = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': '近期问题',
            'client_request_id': str(uuid4()),
        },
    )
    assert result.status_code == 202, result.text
    from zhiheng.modules.chat.context_orm import ContextSnapshot

    async with container.database.sessions() as session:
        snapshot = await session.get(ContextSnapshot, UUID(result.json()['id']))
        assert snapshot.dropped_turns and all(ref['file_id'] != str(file_id) for ref in snapshot.file_refs)


async def test_legacy_regeneration_restores_historical_file_refs_for_execution_guard(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    from zhiheng.modules.chat.context_orm import ContextSnapshot

    uploaded = await admin.post(
        '/api/files?filename=legacy-source.txt',
        content=b'legacy source',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = UUID(uploaded.json()['id'])
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    container = app.state.container
    for index in range(2):
        result = await admin.post(
            f'/api/chat/conversations/{conversation}/runs',
            json={
                'model_id': model_id,
                'message': f'轮次 {index}',
                'file_ids': [str(file_id)] if index == 0 else [],
                'client_request_id': str(uuid4()),
            },
        )
        assert result.status_code == 202, result.text
        await execute_run(container, UUID(result.json()['id']))
    async with container.database.sessions.begin() as session:
        snapshot = await session.get(ContextSnapshot, UUID(result.json()['id']))
        await session.delete(snapshot)
    messages = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    regenerated = await admin.post(
        f'/api/chat/conversations/{conversation}/messages/{messages[-1]["id"]}/regenerate',
        json={'client_request_id': str(uuid4())},
    )
    assert regenerated.status_code == 202, regenerated.text
    async with container.database.sessions.begin() as session:
        (await session.get(FileRecord, file_id)).expires_at = utcnow() - timedelta(seconds=1)
    await execute_run(container, UUID(regenerated.json()['id']))
    assert len(provider.requests) == 2
    assert (await admin.get(f'/api/runs/{regenerated.json()["id"]}')).json()['status'] == 'failed'


@pytest.mark.parametrize('disable_before_execution', [False, True])
async def test_regeneration_keeps_original_skill_revision_and_checks_current_availability(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    disable_before_execution: bool,
) -> None:
    old = await install_skill(admin, 'memory-frozen-skill', 'ORIGINAL FROZEN INSTRUCTION')
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    response = await admin.post(
        f'/api/chat/conversations/{conversation}/runs',
        json={
            'model_id': model_id,
            'message': '技能问题',
            'skill_slugs': [old['slug']],
            'system_prompt': '保留的自定义提示词',
            'client_request_id': str(uuid4()),
        },
    )
    assert response.status_code == 202, response.text
    await execute_run(app.state.container, UUID(response.json()['id']))
    messages = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    updated = await install_skill(admin, 'memory-frozen-skill', 'NEW INSTRUCTION MUST NOT REPLACE FROZEN SNAPSHOT')
    assert updated['revision_id'] != old['revision_id']
    regenerated = await admin.post(
        f'/api/chat/conversations/{conversation}/messages/{messages[-1]["id"]}/regenerate',
        json={'client_request_id': str(uuid4())},
    )
    assert regenerated.status_code == 202, regenerated.text
    history = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    assert messages[0]['skill_snapshots'][0]['revision_id'] == old['revision_id']
    assert history[-1]['skill_snapshots'] == messages[0]['skill_snapshots']
    if disable_before_execution:
        disabled = await admin.patch(
            f'/api/skills/{old["id"]}/status', json={'status': 'disabled', 'expected_version': updated['version']}
        )
        assert disabled.status_code == 200, disabled.text
    await execute_run(app.state.container, UUID(regenerated.json()['id']))
    result = (await admin.get(f'/api/runs/{regenerated.json()["id"]}')).json()
    if disable_before_execution:
        assert result['status'] == 'failed' and len(provider.requests) == 1
    else:
        assert result['status'] == 'completed' and len(provider.requests) == 2
        assert provider.requests[-1]['messages'] == provider.requests[0]['messages']


async def test_early_force_summary_failure_blocks_new_automatic_paid_tasks_after_append(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    container = app.state.container
    container.chat_context.input_tokens = 1000
    provider.chat_summary_output, provider.chat_summary_finish = '不完整', 'length'
    system_prompt = '约束。' * 120
    conversation_id, _, _ = await seed_history(
        admin, app, model_id, count=1, history_padding='重要约束。' * 30, system_prompt=system_prompt
    )
    first = await summary_run(app)
    assert first is not None
    await execute_run(container, first.id)
    next_run = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': '继续问题',
            'system_prompt': system_prompt,
            'client_request_id': str(uuid4()),
        },
    )
    assert next_run.status_code == 202, next_run.text
    await execute_run(container, UUID(next_run.json()['id']))
    async with container.database.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'chat_summary')) == 1
        )
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] == 0


async def test_partial_summary_batches_resume_durable_target_and_finish_first_15_without_new_messages(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    from zhiheng.application.chat_context import ChatContextConfig, ChatContextService

    container = app.state.container
    container.chat_context.input_tokens = 600
    conversation_id, _, _ = await seed_history(admin, app, model_id, count=29)
    finished = False
    for index in range(12):
        run = await summary_run(app)
        assert run is not None
        await execute_native(app, run.id)
        if index == 0:
            # Recreate orchestration after the model commit but before publication.
            service = ChatContextService(
                container.database,
                container.chat,
                container.models,
                container.identity,
                container.files,
                container.admission,
                container.settings.token_limit,
                ChatContextConfig(input_tokens=600),
            )
            container.chat_context, container.admission.context = service, service
        await container.chat_context.reconcile()
        info = (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()
        if info['covered_rounds'] == 15:
            finished = True
            break
    assert finished, info
    assert info['retained_rounds'] == 15
    async with container.database.sessions() as session:
        tasks = list(await session.scalars(select(SummaryTask).order_by(SummaryTask.created_at)))
        assert len(tasks) > 1
        previous = []
        for task in tasks:
            assert task.status == 'published' and task.source_refs[: len(previous)] == previous
            previous = task.source_refs
        batch_count = len(tasks)
    for _ in range(3):
        await container.chat_context.reconcile()
    async with container.database.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'chat_summary'))
            == batch_count
        )
    usage = (await admin.get('/api/usage')).json()
    assert len(usage['records']) == batch_count + 1 and usage['reserved_tokens'] == 0


async def test_knowledge_regeneration_rejects_changed_or_revoked_original_sources_before_main_call(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    from tests.core.integration.test_knowledge import finish_preparation
    from tests.core.integration.test_knowledge_failures import finish_job, ready_base, source

    base = await ready_base(admin, app, provider)
    container = app.state.container
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    original = await admin.post(
        f'/api/chat/conversations/{conversation}/runs',
        json={
            'model_id': model_id,
            'message': 'Which port?',
            'knowledge_base_ids': [base['id']],
            'client_request_id': str(uuid4()),
        },
    )
    prepared = await finish_preparation(admin, app, original.json())
    await execute_run(container, UUID(prepared['run']['id']))
    history = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    old_file_id = UUID(history[-1]['knowledge_sources'][0]['file_id'])
    added = await source(admin, 'second-source.txt', b'The changed accessible source says port 9090.')
    imported = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={'file_ids': [added['id']], 'client_request_id': str(uuid4())},
    )
    assert (await finish_job(admin, app, imported.json()))['status'] == 'completed'
    async with container.database.sessions.begin() as session:
        (await session.get(FileRecord, old_file_id)).expires_at = utcnow() - timedelta(seconds=1)
    before = sum(request['model'] == 'local-text' for request in provider.requests)
    regenerated = await admin.post(
        f'/api/chat/conversations/{conversation}/messages/{history[-1]["id"]}/regenerate',
        json={'client_request_id': str(uuid4())},
    )
    assert regenerated.status_code == 202, regenerated.text
    preparation = regenerated.json()
    # Run real local query embeddings, but never execute a newly admitted main
    # request: source consistency must reject it before admission itself.
    for _ in range(12):
        await container.knowledge_preparation.tick(UUID(preparation['id']))
        async with container.database.sessions() as session:
            pending = list(
                await session.scalars(
                    select(Run.id).where(Run.status == 'pending', Run.resource_type == 'knowledge_embedding')
                )
            )
        for run_id in pending:
            await execute_run(container, run_id)
        preparation = (
            await admin.get(preparation.get('polling_url') or f'/api/knowledge/preparations/{preparation["id"]}')
        ).json()
        if preparation['status'] in {'admitted', 'failed', 'cancelled'}:
            break
    assert preparation['status'] == 'failed', preparation
    assert preparation['error']['code'] == 'regeneration_sources_changed'
    assert sum(request['model'] == 'local-text' for request in provider.requests) == before


async def test_short_chat_detection_dirty_is_silent_without_compaction_work(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id, count=0, reconcile=False)
    async with app.state.container.database.sessions() as session:
        assert (await session.get(ContextState, conversation_id)).dirty is True
    info = (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()
    assert info['status'] == 'full' and info['dirty'] is False


async def test_30th_reply_commit_gap_reports_real_work_before_summary_task_creation(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id, count=29, reconcile=False)
    assert await summary_run(app) is None
    info = (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()
    assert info['status'] == 'summarizing' and info['dirty'] is True


async def test_real_45_chat_runs_trigger_native_arq_summaries_at_30_and_45_exactly_once(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    from zhiheng.worker.main import build_worker

    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    container = app.state.container
    for number in range(1, 46):
        response = await admin.post(
            f'/api/chat/conversations/{conversation}/runs',
            json={
                'model_id': model_id,
                'message': f'真实问题 {number}',
                'client_request_id': str(uuid4()),
            },
        )
        assert response.status_code == 202, response.text
        await execute_run(container, UUID(response.json()['id']))
        if number == 29:
            assert await summary_run(app) is None
        if number in {30, 45}:
            worker = build_worker(container, burst=True)
            try:
                await asyncio.wait_for(worker.async_run(), timeout=25)
            finally:
                await worker.close()
            info = (await admin.get(f'/api/chat/conversations/{conversation}/context')).json()
            assert (info['covered_rounds'], info['retained_rounds']) == (number - 15, 15)
    assert len(provider.requests) == 47
    async with container.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(ChatMessage).where(ChatMessage.conversation_id == UUID(conversation))
            )
            == 90
        )
        tasks = list(await session.scalars(select(SummaryTask).order_by(SummaryTask.created_at)))
        assert [task.covered_count for task in tasks] == [15, 30]
    usage = (await admin.get('/api/usage')).json()
    assert len(usage['records']) == 47 and usage['used_tokens'] == 470 and usage['reserved_tokens'] == 0


async def test_31_round_backlog_legitimately_finishes_two_unique_batches_to_cover_16(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    conversation_id, _, _ = await seed_history(admin, app, model_id, count=30)
    first = await summary_run(app)
    assert first is not None
    await execute_run(app.state.container, first.id)
    second = await summary_run(app)
    assert second is not None and second.id != first.id
    await execute_run(app.state.container, second.id)
    for _ in range(3):
        await app.state.container.chat_context.reconcile()
    info = (await admin.get(f'/api/chat/conversations/{conversation_id}/context')).json()
    assert (info['covered_rounds'], info['retained_rounds']) == (16, 15)
    async with app.state.container.database.sessions() as session:
        tasks = list(await session.scalars(select(SummaryTask).order_by(SummaryTask.created_at)))
        assert [task.covered_count for task in tasks] == [15, 16]
        assert tasks[1].source_refs[:15] == tasks[0].source_refs
        assert len({task.run_id for task in tasks}) == 2
    usage = (await admin.get('/api/usage')).json()
    assert len(provider.requests) == 3
    assert len([row for row in usage['records'] if row['source'] == 'chat_summary']) == 2
    assert usage['used_tokens'] == 30 and usage['reserved_tokens'] == 0


@pytest.mark.parametrize('change', ['rotate', 'rebind', 'upstream', 'window'])
async def test_normal_admitted_chat_keeps_frozen_connection_revision_after_credential_rotation(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    change: str,
) -> None:
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    admitted = await admin.post(
        f'/api/chat/conversations/{conversation}/runs',
        json={
            'model_id': model_id,
            'message': '冻结已接纳的模型配置' * 100,
            'client_request_id': str(uuid4()),
        },
    )
    assert admitted.status_code == 202, admitted.text
    run_id = UUID(admitted.json()['id'])
    async with app.state.container.database.sessions() as session:
        before = (await session.get(Run, run_id)).payload
        connection_id = before['binding']['connection_id']
    if change == 'rotate':
        changed = await admin.patch(
            f'/api/models/connections/{connection_id}', json={'api_key': 'rotated-local-test-key'}
        )
    elif change == 'rebind':
        new_connection = await admin.post(
            '/api/models/connections',
            json={
                'name': 'New unavailable endpoint',
                'base_url': 'http://127.0.0.1:9/v1',
                'api_key': 'rebound-local-test-key',
            },
        )
        assert new_connection.status_code == 201, new_connection.text
        changed = await admin.patch(f'/api/models/{model_id}', json={'connection_id': new_connection.json()['id']})
    elif change == 'upstream':
        changed = await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'changed-upstream'})
    else:
        changed = await admin.patch(f'/api/models/{model_id}', json={'context_window_tokens': 512})
    assert changed.status_code == 200, changed.text
    await execute_run(app.state.container, run_id)
    result = (await admin.get(f'/api/runs/{run_id}')).json()
    assert result['status'] == 'completed', result
    assert len(provider.requests) == 1
    assert provider.requests[0]['model'] == 'local-text'
    assert provider.request_headers[0]['authorization'] == 'Bearer local-test-key'
    async with app.state.container.database.sessions() as session:
        assert (await session.get(Run, run_id)).payload == before


@pytest.mark.parametrize('operation', ['automatic_cancel', 'manual_retry'])
async def test_summary_reconciliation_never_takes_state_while_main_completion_holds_account(  # noqa: PLR0915 - one deterministic transaction barrier owns the test schedule
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    container = app.state.container
    conversation_id, _, _ = await seed_history(admin, app, model_id)
    summary = await summary_run(app)
    assert summary is not None
    if operation == 'manual_retry':
        provider.chat_summary_output, provider.chat_summary_finish = '不完整', 'length'
        await execute_run(container, summary.id)
    accepted = await admin.post(
        f'/api/chat/conversations/{conversation_id}/runs',
        json={
            'model_id': model_id,
            'message': '正在完成的下一轮',
            'client_request_id': str(uuid4()),
        },
    )
    assert accepted.status_code == 202, accepted.text
    main_id = UUID(accepted.json()['id'])
    entered, release = asyncio.Event(), asyncio.Event()
    holder_pid: list[int] = []
    original_complete = container.chat.complete

    async def paused_complete(session: 'AsyncSession', **kwargs: object) -> None:
        holder_pid.append(await session.scalar(text('SELECT pg_backend_pid()')))
        entered.set()
        await release.wait()
        await original_complete(session, **kwargs)

    monkeypatch.setattr(container.chat, 'complete', paused_complete)
    execution = asyncio.create_task(execute_run(container, main_id))
    action = None
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        async with container.database.sessions() as session:
            state = await session.get(ContextState, conversation_id)
            expected_state = (
                state.dirty,
                state.pending_task_id,
                state.summary_task_id,
                state.version,
                state.target_covered_count,
                state.error_code,
                state.last_run_id,
            )
        if operation == 'automatic_cancel':
            disabled = await admin.post(f'/api/models/{model_id}/disable', json={})
            assert disabled.status_code == 200, disabled.text
            action = asyncio.create_task(container.chat_context.reconcile())
        else:
            action = asyncio.create_task(
                admin.post(
                    f'/api/chat/conversations/{conversation_id}/context/retry', json={'client_request_id': str(uuid4())}
                )
            )
        # The old path blocks on this account while holding ContextState. Releasing
        # completion then exposes the inverse account -> state dependency. A safe
        # deferral returns before taking State, so it never appears in the waiters.
        async with container.database.sessions() as observer:
            for _ in range(100):
                if action.done():
                    break
                blocked = await observer.scalar(
                    text('SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :blocker = ANY(pg_blocking_pids(pid)))'),
                    {'blocker': holder_pid[0]},
                )
                if blocked:
                    break
                await asyncio.sleep(0.01)
        if action.done():
            async with container.database.sessions() as session:
                state = await session.get(ContextState, conversation_id)
                assert (
                    state.dirty,
                    state.pending_task_id,
                    state.summary_task_id,
                    state.version,
                    state.target_covered_count,
                    state.error_code,
                    state.last_run_id,
                ) == expected_state
        release.set()
        _, response = await asyncio.wait_for(asyncio.gather(execution, action), timeout=5)
        if operation == 'manual_retry':
            assert response.status_code == 409 and response.json()['error']['code'] == 'conversation_busy'
        result = (await admin.get(f'/api/runs/{main_id}')).json()
        assert result['status'] == 'completed', result
        usage = (await admin.get('/api/usage')).json()
        assert usage['reserved_tokens'] == 0
        async with container.database.sessions() as session:
            assert (
                await session.scalar(select(func.count()).select_from(Run).where(Run.resource_type == 'chat_summary'))
                == 1
            )
    finally:
        release.set()
        for task in (execution, action):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (execution, action) if task is not None), return_exceptions=True)
