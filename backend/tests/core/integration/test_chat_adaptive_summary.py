"""Freeze and execute capacity-based summary settings through real chat admissions."""

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select

from tests.core.chat_support import admit, completed, conversation, execute_with_summaries, frozen_context
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.chat.context_orm import SummaryTask
from zhiheng.modules.chat.public import ChatExecutionSpec

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests.core.provider import ProviderServer


@pytest.fixture
def settings(settings: Settings, request: pytest.FixtureRequest) -> Settings:
    return Settings.model_validate(
        {
            **settings.model_dump(),
            **{f'chat_summary_{part}_tokens': None for part in ('trigger', 'keep', 'input', 'output')},
            'provider_timeout_seconds': 5,
            **getattr(request, 'param', {}),
        }
    )


async def test_megabyte_model_keeps_20k_history_without_premature_summary(
    admin: 'httpx.AsyncClient',
    app: 'FastAPI',
    provider: 'ProviderServer',
    model_id: str,
) -> None:
    changed = await admin.patch(f'/api/models/{model_id}', json={'context_window_tokens': 1000000})
    assert changed.status_code == 200, changed.text
    cid = await conversation(admin)
    message = '历史细节' * 1500
    first = await admit(admin, cid, model_id, message)
    snapshot = await frozen_context(app, first['id'])
    spec = ChatExecutionSpec.model_validate(snapshot.agent_context)
    assert spec is not None
    assert spec.trigger_tokens > 600000 and spec.keep_tokens > 190000
    assert spec.summary_input_tokens > 900000
    await completed(admin, app, first['id'])
    second = await admit(admin, cid, model_id, message + '第二部分')
    await completed(admin, app, second['id'])
    third = await admit(admin, cid, model_id, 'Continue with the original detail')
    await completed(admin, app, third['id'])
    assert len(provider.requests) == 3
    assert message in str(provider.requests[-1]['messages'])
    async with app.state.container.database.sessions() as session:
        assert not list(await session.scalars(select(SummaryTask)))
    context = (await admin.get(f'/api/chat/conversations/{cid}/context')).json()
    assert context['last_run_context']['summary_trigger_tokens'] > 600000
    assert context['last_run_context']['retained_token_budget'] > 190000


@pytest.mark.parametrize(
    'settings',
    [{'chat_summary_trigger_tokens': 256, 'chat_summary_keep_tokens': 64, 'chat_summary_output_tokens': 64}],
    indirect=True,
)
async def test_manual_trigger_still_summarizes_a_large_window_without_changing_on_replay(
    admin: 'httpx.AsyncClient',
    app: 'FastAPI',
    provider: 'ProviderServer',
    model_id: str,
) -> None:
    assert (await admin.patch(f'/api/models/{model_id}', json={'context_window_tokens': 1000000})).status_code == 200
    cid = await conversation(admin)
    first = await admit(admin, cid, model_id, 'history ' * 80)
    await execute_with_summaries(app, first['id'])
    assert (await admin.get('/api/runs/' + first['id'])).json()['status'] == 'completed'
    spec = ChatExecutionSpec.model_validate((await frozen_context(app, first['id'])).agent_context)
    assert spec is not None and spec.trigger_tokens == 256 and spec.keep_tokens == 64
    assert spec.summary_input_tokens > 900000
    second = await admit(admin, cid, model_id, 'Continue')
    await execute_with_summaries(app, second['id'])
    assert (await admin.get('/api/runs/' + second['id'])).json()['status'] == 'completed'
    async with app.state.container.database.sessions() as session:
        summaries = list(await session.scalars(select(SummaryTask)))
        assert summaries and all(item.status == 'published' for item in summaries)
    calls = len(provider.requests)
    from zhiheng.worker.execution import execute_run

    await execute_run(app.state.container, UUID(second['id']))
    assert len(provider.requests) == calls


async def test_switching_models_recomputes_new_policy_but_preserves_the_old_execution(
    admin: 'httpx.AsyncClient',
    app: 'FastAPI',
    provider: 'ProviderServer',
    model_id: str,
) -> None:
    assert (await admin.patch(f'/api/models/{model_id}', json={'context_window_tokens': 1000000})).status_code == 200
    cid = await conversation(admin)
    first = await admit(admin, cid, model_id, 'First detail')
    await completed(admin, app, first['id'])
    before = ChatExecutionSpec.model_validate((await frozen_context(app, first['id'])).agent_context)
    assert before is not None
    assert (await admin.patch(f'/api/models/{model_id}', json={'context_window_tokens': 32768})).status_code == 200
    second = await admit(admin, cid, model_id, 'Next detail')
    after = ChatExecutionSpec.model_validate((await frozen_context(app, second['id'])).agent_context)
    assert after is not None and 20000 < after.trigger_tokens < 23000
    assert after.summary_input_tokens < 32768
    assert ChatExecutionSpec.model_validate((await frozen_context(app, first['id'])).agent_context) == before
    await completed(admin, app, second['id'])


async def _create_priced_distinct_models(
    admin: 'httpx.AsyncClient', app: 'FastAPI', provider: 'ProviderServer', model_id: str
) -> tuple[str, UUID]:
    from tests.core.integration.test_billing_runtime import prepare as prepare_points

    assert (await admin.patch(f'/api/models/{model_id}', json={'context_window_tokens': 1000000})).status_code == 200
    connection = await admin.post(
        '/api/models/connections',
        json={
            'name': 'Distinct small-model connection',
            'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
            'api_key': 'small-model-local-key',
        },
    )
    assert connection.status_code == 201, connection.text
    created = await admin.post(
        '/api/models',
        json={
            'name': 'Distinct small-window model',
            'connection_id': connection.json()['id'],
            'upstream_model': 'adaptive-small-local',
            'max_output_tokens': 64,
            'context_window_tokens': 16384,
        },
    )
    assert created.status_code == 201, created.text
    small_id = created.json()['id']
    assert small_id != model_id
    actor = await prepare_points(admin, app, model_id)
    await prepare_points(admin, app, small_id, points='0')
    return small_id, actor.id


async def _assert_model_switch_native_charges(
    app: 'FastAPI', first_id: str, second_id: str, model_ids: tuple[str, str], owner_id: UUID
) -> UUID:
    from decimal import Decimal

    from sqlalchemy import func

    from zhiheng.modules.billing.orm import CreditHold, CreditLedger, Invocation
    from zhiheng.modules.models.public import ModelRequest
    from zhiheng.runtime.orm import Run

    container = app.state.container
    async with container.database.sessions() as session:
        task = await session.scalar(select(SummaryTask).where(SummaryTask.parent_run_id == UUID(second_id)))
        assert task is not None and task.status == 'published' and task.run_id is not None
        summary_id = task.run_id
        runs = list(await session.scalars(select(Run).order_by(Run.created_at, Run.id)))
        assert len(runs) == 3
        models = {run.id: ModelRequest.model_validate(run.payload).binding.model_id for run in runs}
        assert models[UUID(first_id)] == UUID(model_ids[0])
        assert models[UUID(second_id)] == models[summary_id] == UUID(model_ids[1])
        invocations = list(await session.scalars(select(Invocation)))
        holds = list(await session.scalars(select(CreditHold)))
        assert len(invocations) == len(holds) == 3
        for invocation in invocations:
            assert invocation.model_id == models[invocation.run_id]
            assert invocation.status == 'known' and invocation.charged_tokens == 10
        assert all(hold.status == 'settled' and hold.charged_points == Decimal('0.0018') for hold in holds)
        assert (
            await session.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.kind == 'settle'))
            == 3
        )
        wallet = await container.credits.queries.summary(session, owner_id)
        assert wallet.used_points == Decimal('0.0054') and wallet.reserved_points == 0

    return summary_id


async def _assert_preserved_model_switch_checkpoint(
    app: 'FastAPI', cid: str, first_snapshot: dict, original_text: str
) -> None:
    from tests.core.chat_support import memory_head
    from zhiheng.infrastructure.agents.adapters.checkpoints import CheckpointStore
    from zhiheng.modules.chat.public import ContextSnapshotValue

    first_head = first_snapshot['agent_checkpoint_id']
    first_input = first_snapshot['agent_context']['chat_context_model_checkpoint_id']
    assert (
        ContextSnapshotValue.model_validate(await frozen_context(app, first_snapshot['run_id'])).model_dump(mode='json')
        == first_snapshot
    )
    store = CheckpointStore(app.state.container.settings.database_url.get_secret_value())
    async with store.saver() as saver:
        old_head = await saver.aget_tuple(store.config(UUID(cid), first_head))
        old_input = await saver.aget_tuple(store.config(UUID(cid), first_input))
        assert old_head is not None and old_input is not None
        assert original_text in [message.content for message in old_input.checkpoint['channel_values']['messages']]
    assert await memory_head(app, cid) != first_head


async def test_switching_distinct_models_recomputes_policy_and_charges_each_native_run_once(
    admin: 'httpx.AsyncClient',
    app: 'FastAPI',
    provider: 'ProviderServer',
    model_id: str,
) -> None:
    from uuid import uuid4

    from sqlalchemy import func

    from tests.core.chat_support import memory_head
    from zhiheng.modules.billing.orm import CreditLedger, Invocation
    from zhiheng.modules.chat.public import ContextSnapshotValue
    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    small_id, owner_id = await _create_priced_distinct_models(admin, app, provider, model_id)
    provider.chat_summary_output = 'Retained the original historical facts.'
    cid = await conversation(admin)
    original_text = '历史事实' * 1500
    first = await admit(admin, cid, model_id, original_text)
    await completed(admin, app, first['id'])
    first_snapshot = ContextSnapshotValue.model_validate(await frozen_context(app, first['id'])).model_dump(mode='json')
    first_head = await memory_head(app, cid)
    first_input = first_snapshot['agent_context']['chat_context_model_checkpoint_id']
    assert first_head and first_input
    assert first_snapshot['agent_context']['trigger_tokens'] > 600000

    key = str(uuid4())
    second = await admit(admin, cid, small_id, 'Continue using the smaller selected model.', request_id=key)
    replay = await admit(admin, cid, small_id, 'Continue using the smaller selected model.', request_id=key)
    assert replay['id'] == second['id']
    prepared = await frozen_context(app, second['id'])
    spec = ChatExecutionSpec.model_validate(prepared.agent_context)
    assert spec.base_checkpoint_id == first_head and spec.expected_head == first_head
    assert 10000 < spec.trigger_tokens < 12000 and spec.keep_tokens < 4000
    assert spec.summary_input_tokens < 16384
    assert (
        ContextSnapshotValue.model_validate(await frozen_context(app, first['id'])).model_dump(mode='json')
        == first_snapshot
    )
    await execute_with_summaries(app, second['id'])
    result = (await admin.get('/api/runs/' + second['id'])).json()
    assert result['status'] == 'completed', result
    assert len(provider.requests) == 3
    assert provider.requests[0]['model'] == 'local-text'
    assert [request['model'] for request in provider.requests[1:]] == ['adaptive-small-local'] * 2
    assert [headers['authorization'] for headers in provider.request_headers[1:]] == [
        'Bearer small-model-local-key',
        'Bearer small-model-local-key',
    ]

    container = app.state.container
    summary_id = await _assert_model_switch_native_charges(
        app, first['id'], second['id'], (model_id, small_id), owner_id
    )
    await _assert_preserved_model_switch_checkpoint(app, cid, first_snapshot, original_text)
    calls = len(provider.requests)
    assert (await admit(admin, cid, small_id, 'Continue using the smaller selected model.', request_id=key))[
        'id'
    ] == second['id']
    await execute_run(container, UUID(second['id']))
    await execute_run(container, summary_id)
    assert len(provider.requests) == calls
    async with container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Run)) == 3
        assert await session.scalar(select(func.count()).select_from(Invocation)) == 3
        assert (
            await session.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.kind == 'settle'))
            == 3
        )
