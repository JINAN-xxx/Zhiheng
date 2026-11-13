"""Unified historical metering and points across real admission/worker paths."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from tests.core.integration.test_agents import config
from tests.core.integration.test_chat_runs import admit as admit_chat
from tests.core.integration.test_knowledge import VectorFixture, embedding_model
from tests.core.integration.test_knowledge_failures import finish_job
from tests.core.integration.test_knowledge_failures import source as upload_source
from tests.core.integration.test_public_protocol import drive
from tests.core.integration.test_work import member_client, published, task, turn
from zhiheng.modules.billing.contracts import CreditAdjustment, CreditRateCreate, CreditReconcile, CreditStatePatch
from zhiheng.modules.billing.orm import CreditHold, Invocation, UsageAccount
from zhiheng.modules.developer.orm import KeyMinuteBucket
from zhiheng.modules.models.public import ModelMessage, ModelRequest
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow
from zhiheng.worker.execution import execute_run

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests.core.provider import ProviderServer
    from zhiheng.bootstrap.container import Container


async def owner(admin: httpx.AsyncClient) -> Principal:
    return Principal(UUID((await admin.get('/api/auth/me')).json()['id']), 'admin', True)


async def prepare(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    *,
    points: str = '100',
    free: bool = False,
    priced: bool = True,
) -> Principal:
    actor, container = await owner(admin), app.state.container
    async with container.database.sessions.begin() as session:
        if Decimal(points) > 0:
            await container.credits.adjust(
                session,
                actor,
                actor.id,
                CreditAdjustment(
                    kind='grant', points=points, reason='Runtime test wallet', client_request_id=uuid4().hex
                ),
            )
        if priced:
            await container.credits.create_rate(
                session,
                actor,
                UUID(model_id),
                CreditRateCreate(
                    billing_mode='token',
                    is_free=free,
                    input_points='0' if free else '100',
                    output_points='0' if free else '500',
                    cache_points='0' if free else '25',
                    reason='Runtime test price',
                    client_request_id=uuid4().hex,
                ),
                model_type='chat',
            )
        state = await container.credits.state(session)
        if not state.enabled:
            await container.credits.set_state(
                session,
                actor,
                CreditStatePatch(
                    enabled=True,
                    expected_version=state.version,
                    reason='Runtime test switch',
                    client_request_id=uuid4().hex,
                ),
            )
    return actor


async def reserve(
    container: Container, actor: Principal, model_id: str, *, run_id: UUID | None = None, key_id: UUID | None = None
) -> UUID:
    run_id = run_id or uuid4()
    async with container.database.sessions.begin() as session:
        await container.usage.reserve(
            session,
            user_id=actor.id,
            run_id=run_id,
            model_id=UUID(model_id),
            source='connection_test',
            tokens=2000,
            token_limit=1,
            estimated_input_tokens=1000,
            max_output_tokens=1000,
            key_id=key_id,
        )
    return run_id


async def issue(admin: httpx.AsyncClient, model_id: str, **limits: object) -> dict[str, Any]:
    application = await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'Points SDK'})
    assert application.status_code == 201, application.text
    result = await admin.post(
        f'/api/developer/applications/{application.json()["id"]}/keys',
        json={'name': 'Points SDK', 'model_ids': [model_id], **limits},
    )
    assert result.status_code == 201, result.text
    return result.json()


async def test_unified_example_bypasses_old_token_gate_but_preserves_statistics(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    run = await reserve(container, actor, model_id)
    async with container.database.sessions.begin() as session:
        summary = await container.credits.summary(session, actor.id)
        assert summary.reserved_points == Decimal('0.6') and summary.available_points == Decimal('99.4')
        result = await container.usage.settle(session, run, input_tokens=1000, output_tokens=200)
        assert result.points_enforced and result.status == 'known'
    async with container.database.sessions() as session:
        summary = await container.credits.summary(session, actor.id)
        legacy = await container.usage.summary(session, actor.id, token_limit=1)
        assert summary.used_points == Decimal('0.2') and summary.available_points == Decimal('99.8')
        assert legacy.used_tokens == 1200 and legacy.reserved_tokens == 0 and legacy.limit_tokens == 1


@pytest.mark.parametrize('source', ['chat', 'connection_test', 'agent_preview', 'work_turn', 'public'])
async def test_each_user_entry_reserves_from_frozen_input_then_local_worker_settles(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    source: str,
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    if source == 'chat':
        _, run = await admit_chat(admin, model_id)
    elif source == 'connection_test':
        response = await admin.post(f'/api/models/{model_id}/test', json={'client_request_id': str(uuid4())})
        assert response.status_code == 202, response.text
        run = response.json()
    elif source == 'agent_preview':
        agent = (await admin.post('/api/agents', json=config(model_id))).json()
        response = await admin.post(
            f'/api/agents/{agent["id"]}/preview',
            json={'client_request_id': str(uuid4()), 'messages': [{'role': 'user', 'content': 'hello'}]},
        )
        assert response.status_code == 202, response.text
        run = response.json()
    elif source == 'work_turn':
        agent = await published(admin, model_id)
        work = await task(admin, agent)
        response = await admin.post(f'/api/work/tasks/{work["id"]}/runs', json=turn(work, agent))
        assert response.status_code == 202, response.text
        run = response.json()
    else:
        key = await issue(admin, model_id)
        async with container.database.sessions.begin() as session:
            snapshot, _ = await container.public_runs.admit(
                session,
                key['api_key'],
                model_id=UUID(model_id),
                operation='chat',
                messages=(ModelMessage('user', 'hello'),),
                options={},
                token_limit=1,
            )
            run = snapshot.model_dump(mode='json')
    run_id = UUID(run['id'])
    async with container.database.sessions() as session:
        row = await container.runs.resource_payload(session, run_id, source, UUID(run['resource_id']))
        request = ModelRequest.model_validate(row)
        estimated = sum(
            len(m.content.encode()) + 16 + sum(image.estimated_tokens for image in m.images) for m in request.messages
        )
        hold = await session.get(CreditHold, run_id)
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run_id))
        assert hold.reserved_points == (Decimal(estimated) * 100 + request.binding.max_output_tokens * 500) / 1000000
        assert invocation.points_enforced and hold.user_id == actor.id
    await execute_run(container, run_id)
    async with container.database.sessions() as session:
        hold = await session.get(CreditHold, run_id)
        assert hold.status == 'settled' and hold.charged_points == Decimal('0.0018')
        assert (await container.credits.summary(session, actor.id)).reserved_points == 0
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    'free,priced,expected',
    [(False, True, 'credits_insufficient'), (False, False, 'credit_rate_missing'), (True, True, None)],
)
async def test_empty_wallet_missing_rate_and_explicit_free_admission(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    free: bool,
    priced: bool,
    expected: str | None,
) -> None:
    actor = await prepare(admin, app, model_id, points='0', free=free, priced=priced)
    if expected:
        with pytest.raises(AppError) as rejected:
            await reserve(app.state.container, actor, model_id)
        assert rejected.value.code == expected
        async with app.state.container.database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Invocation)) == 0
    else:
        run = await reserve(app.state.container, actor, model_id)
        async with app.state.container.database.sessions.begin() as session:
            await app.state.container.usage.settle(session, run, input_tokens=1000, output_tokens=200)
            assert (await app.state.container.credits.summary(session, actor.id)).used_points == 0
    assert provider.requests == []


async def test_unknown_reconcile_releases_both_ledgers_and_rolls_back_bad_version(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    run = await reserve(container, actor, model_id)
    async with container.database.sessions.begin() as session:
        assert (await container.usage.settle(session, run)).status == 'unknown'
    async with container.database.sessions.begin() as session:
        hold = await session.get(CreditHold, run)
        body = CreditReconcile(
            expected_version=hold.version,
            reason='Confirmed provider usage',
            client_request_id=uuid4().hex,
            input_tokens=1000,
            output_tokens=200,
        )
        with pytest.raises(AppError):
            await container.usage.reconcile(session, actor, run, body.model_copy(update={'expected_version': 999}))
    async with container.database.sessions.begin() as session:
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run))
        account = await session.get(UsageAccount, actor.id)
        assert invocation.status == 'unknown' and account.reserved_tokens == 2000
        first = await container.usage.reconcile(session, actor, run, body)
        repeated = await container.usage.reconcile(session, actor, run, body)
        assert first.charged_points == repeated.charged_points == Decimal('0.2')
    async with container.database.sessions() as session:
        account = await session.get(UsageAccount, actor.id)
        assert account.reserved_tokens == 0 and account.used_tokens == 1200
        assert (await container.credits.summary(session, actor.id)).available_points == Decimal('99.8')


async def test_key_points_limit_rotation_and_shared_parent_concurrency(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id, points='1')
    first = await issue(admin, model_id, monthly_points_limit='0.75')
    rotated = await admin.post(
        f'/api/developer/applications/{first["application_id"]}/keys/{first["id"]}/rotate',
        json={'grace_period_seconds': 0},
    )
    assert rotated.status_code == 201 and Decimal(rotated.json()['monthly_points_limit']) == Decimal('0.75')
    second = await issue(admin, model_id, monthly_points_limit=None)

    async def attempt(key: dict[str, Any]) -> UUID | str:
        try:
            return await reserve(app.state.container, actor, model_id, key_id=UUID(key['id']))
        except AppError as error:
            return error.code

    results = await asyncio.wait_for(asyncio.gather(attempt(rotated.json()), attempt(second)), 5)
    assert sum(isinstance(value, UUID) for value in results) == 1 and 'credits_insufficient' in results


async def test_points_only_public_api_keeps_key_limits_and_requires_currency_rate_for_money_budget(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    await prepare(admin, app, model_id)
    key = await issue(admin, model_id, rpm_limit=1)
    request = {'model': model_id, 'messages': [{'role': 'user', 'content': 'hello'}]}
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    response = await drive(
        app.state.container, asyncio.create_task(admin.post('/v1/chat/completions', headers=headers, json=request))
    )
    assert response.status_code == 200, response.text
    limited = await admin.post('/v1/chat/completions', headers=headers, json=request)
    assert limited.status_code == 429 and limited.json()['error']['code'] == 'rate_limit_exceeded'
    budget = await issue(admin, model_id, monthly_budget='1')
    rejected = await admin.post(
        '/v1/chat/completions', headers={'Authorization': 'Bearer ' + budget['api_key']}, json=request
    )
    assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'money_rate_required'
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Invocation)) == 1
        assert await session.scalar(select(func.sum(KeyMinuteBucket.requests))) == 1
    assert len(provider.requests) == 1


async def test_points_public_embedding_and_rerank_use_their_own_units(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    connection_id = (await admin.get('/api/models')).json()[0]['connection_id']
    for kind, endpoint, payload, charged in (
        ('embedding', 'embeddings', {'input': ['first', 'second'], 'dimensions': 2}, Decimal('0.0006')),
        ('rerank', 'rerank', {'query': 'first', 'documents': ['first', 'second']}, Decimal('20')),
    ):
        created = await admin.post(
            '/api/models',
            json={
                'name': kind,
                'connection_id': connection_id,
                'upstream_model': kind,
                'model_type': kind,
            },
        )
        assert created.status_code == 201, created.text
        model = created.json()['id']
        async with container.database.sessions.begin() as session:
            await container.credits.create_rate(
                session,
                actor,
                UUID(model),
                CreditRateCreate(
                    billing_mode='search_unit' if kind == 'rerank' else 'token',
                    input_points='100' if kind == 'embedding' else '0',
                    search_points='10' if kind == 'rerank' else '0',
                    reason='Structured test rate',
                    client_request_id=uuid4().hex,
                ),
                model_type=kind,
            )
        key = await issue(admin, model)
        response = await drive(
            container,
            asyncio.create_task(
                admin.post(
                    '/v1/' + endpoint,
                    headers={'Authorization': 'Bearer ' + key['api_key']},
                    json={'model': model, **payload},
                )
            ),
        )
        assert response.status_code == 200, response.text
        async with container.database.sessions() as session:
            hold = await session.scalar(select(CreditHold).where(CreditHold.model_id == UUID(model)))
            assert hold.status == 'settled' and hold.charged_points == charged
            if kind == 'rerank':
                assert hold.reserved_points == Decimal('20')
    assert len(provider.requests) == 2


async def test_points_index_embedding_and_retrieval_embedding_are_both_metered(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await embedding_model(admin, provider)
    actor = await prepare(admin, app, model)
    ranker = (await admin.get('/api/knowledge/settings')).json()['default_rerank_model_id']
    price = await admin.post(
        '/api/billing/models/' + ranker + '/rates',
        json={'billing_mode': 'search_unit', 'is_free': True, 'client_request_id': str(uuid4())},
    )
    assert price.status_code == 201, price.text
    container = app.state.container
    container.knowledge_jobs.vectors = container.knowledge_preparation.vectors = VectorFixture()
    created = await admin.post('/api/knowledge/bases', json={'name': 'Points index', 'embed_model_id': model})
    assert created.status_code == 201, created.text
    endpoint = '/api/knowledge/bases/' + created.json()['id']
    file = await upload_source(admin, 'points.txt', b'Knowledge points are metered.')
    admitted = await admin.post(
        endpoint + '/documents', json={'file_ids': [file['id']], 'client_request_id': str(uuid4())}
    )
    assert admitted.status_code == 202, admitted.text
    indexed = await finish_job(admin, app, admitted.json())
    assert indexed['status'] == 'completed', indexed
    response = await admin.post(endpoint + '/query', json={'query': 'points', 'client_request_id': str(uuid4())})
    assert response.status_code == 202, response.text
    preparation = response.json()
    for _ in range(12):
        await container.knowledge_preparation.tick(UUID(preparation['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Invocation.run_id).where(Invocation.status == 'pending')))
        for run_id in pending:
            await execute_run(container, run_id)
        preparation = (await admin.get('/api/knowledge/preparations/' + preparation['id'])).json()
        if preparation['status'] in {'completed', 'failed'}:
            break
    assert preparation['status'] == 'completed', preparation
    async with container.database.sessions() as session:
        holds = list(await session.scalars(select(CreditHold)))
        assert {hold.source for hold in holds} == {'knowledge_embedding', 'knowledge_retrieval'}
        assert len(holds) == 3 and all(hold.status == 'settled' for hold in holds)
        assert (await container.credits.summary(session, actor.id)).used_points == Decimal('0.0012')
    assert len(provider.requests) == 3


async def test_points_graph_callbacks_keep_real_ownership_and_each_model_step_reservation(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from tests.core.graph_fixtures import graph_base
    from zhiheng.application.knowledge.graph.runs import GraphModelCall
    from zhiheng.modules.knowledge.public import GraphBuildRequest

    container = app.state.container
    container.knowledge_jobs.vectors = container.knowledge_preparation.vectors = VectorFixture()
    base = await graph_base(admin, app, provider)
    models = (await admin.get('/api/models')).json()
    for index, model in enumerate(models):
        actor = await prepare(admin, app, model['id'], points='100' if index == 0 else '0')
    async with container.database.sessions.begin() as session:
        job = await container.graph.admit(
            session, actor, UUID(base['id']), GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )
    async with container.database.sessions.begin() as session:
        claim = await container.graph_store.claim(session, job.id)
    assert claim is not None
    container.graph_runs.start_attempt(claim)
    try:
        for role, operation in [('extract', 'generation'), ('embedding', 'embedding')]:
            call = GraphModelCall(
                role=role, operation=operation, messages=(ModelMessage('user', 'hello'),), output_tokens=32
            )
            callback = asyncio.create_task(container.graph_runs.invoke(claim, call))
            try:
                for _ in range(150):
                    if callback.done():
                        break
                    async with container.database.sessions() as session:
                        pending = list(
                            await session.scalars(
                                select(Invocation.run_id).where(
                                    Invocation.source == 'knowledge_graph', Invocation.status == 'pending'
                                )
                            )
                        )
                    for run_id in pending:
                        await execute_run(container, run_id)
                    await asyncio.sleep(0.01)
                assert (await asyncio.wait_for(callback, 2)).status == 'completed'
            finally:
                if not callback.done():
                    callback.cancel()
                await asyncio.gather(callback, return_exceptions=True)
        async with container.database.sessions() as session:
            holds = list(await session.scalars(select(CreditHold).where(CreditHold.source == 'knowledge_graph')))
            assert len(holds) == 2 and all(hold.status == 'settled' and hold.user_id == actor.id for hold in holds)
            assert sum(hold.charged_points for hold in holds) == Decimal('0.0024')
    finally:
        container.graph_runs.release(claim.attempt_id)


async def test_mode_switch_does_not_retroactively_bill_old_calls_or_release_existing_point_holds(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    container, actor, old = app.state.container, await owner(admin), uuid4()
    async with container.database.sessions.begin() as session:
        await container.usage.reserve(
            session, user_id=actor.id, run_id=old, model_id=UUID(model_id), source='chat', tokens=20, token_limit=100
        )
    await prepare(admin, app, model_id)
    async with container.database.sessions.begin() as session:
        await container.usage.require_reservation(session, old, actor.id, UUID(model_id))
        assert not (await container.usage.settle(session, old, input_tokens=10, output_tokens=2)).points_enforced
        assert (await container.credits.summary(session, actor.id)).used_points == 0
    new = await reserve(container, actor, model_id)
    async with container.database.sessions.begin() as session:
        state = await container.credits.state(session)
        await container.credits.set_state(
            session,
            actor,
            CreditStatePatch(
                enabled=False,
                expected_version=state.version,
                reason='Disable new point admission',
                client_request_id=uuid4().hex,
            ),
        )
    async with container.database.sessions.begin() as session:
        await container.usage.require_reservation(session, new, actor.id, UUID(model_id))
        await container.usage.settle(session, new, input_tokens=1000, output_tokens=200)
        assert (await container.credits.summary(session, actor.id)).used_points == Decimal('0.2')


async def test_failed_credit_admission_restores_key_bucket_inside_the_callers_transaction(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    actor = await prepare(admin, app, model_id, points='0')
    key = await issue(admin, model_id, rpm_limit=1)
    container = app.state.container
    async with container.database.sessions.begin() as session:
        fields = {
            'user_id': actor.id,
            'model_id': UUID(model_id),
            'source': 'public',
            'tokens': 2000,
            'token_limit': 1,
            'key_id': UUID(key['id']),
            'estimated_input_tokens': 1000,
            'max_output_tokens': 1000,
        }
        with pytest.raises(AppError) as rejected:
            await container.usage.reserve(session, run_id=uuid4(), **fields)
        assert rejected.value.code == 'credits_insufficient'
        assert await session.scalar(select(func.count()).select_from(Invocation)) == 0
        assert await session.scalar(select(func.count()).select_from(KeyMinuteBucket)) == 0
        await container.credits.adjust(
            session,
            actor,
            actor.id,
            CreditAdjustment(kind='grant', points='1', reason='Retry funded admission', client_request_id=uuid4().hex),
        )
        await container.usage.reserve(session, run_id=uuid4(), **fields)
        assert await session.scalar(select(func.sum(KeyMinuteBucket.requests))) == 1


async def test_public_agent_can_use_points_price_without_historical_money_rate(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
) -> None:
    await prepare(admin, app, model_id)
    key = await issue(admin, model_id)
    created = await admin.post('/api/agents', json=config(model_id, application_id=key['application_id']))
    assert created.status_code == 201, created.text
    agent = created.json()
    published_agent = await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})
    assert published_agent.status_code == 200, published_agent.text
    response = await drive(
        app.state.container,
        asyncio.create_task(
            admin.post(
                f'/v1/agents/{agent["id"]}/chat/completions',
                headers={'Authorization': 'Bearer ' + key['api_key']},
                json={'messages': [{'role': 'user', 'content': 'hello'}]},
            )
        ),
    )
    assert response.status_code == 200, response.text
    async with app.state.container.database.sessions() as session:
        hold = await session.scalar(select(CreditHold).where(CreditHold.source == 'agent_invocation'))
        assert hold.status == 'settled' and hold.charged_points == Decimal('0.0018')
    assert len(provider.requests) == 1


async def test_admin_work_turn_keeps_original_owners_wallet_and_resource_authorization(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    container = app.state.container
    actor = await prepare(admin, app, model_id)
    async with member_client(app) as member:
        agent = await published(member, model_id)
        work = await task(member, agent)
        member_id = UUID(work['owner_id'])
        async with container.database.sessions.begin() as session:
            await container.credits.adjust(
                session,
                actor,
                member_id,
                CreditAdjustment(kind='grant', points='1', reason='Owner allocation', client_request_id=uuid4().hex),
            )
            before = await container.credits.summary(session, member_id)
        denied = await admin.post(f'/api/work/tasks/{work["id"]}/runs', json=turn(work, agent))
        assert denied.status_code == 404, denied.text
        async with container.database.sessions() as session:
            assert await container.credits.summary(session, member_id) == before
        response = await member.post(f'/api/work/tasks/{work["id"]}/runs', json=turn(work, agent))
        assert response.status_code == 202, response.text
        await execute_run(container, UUID(response.json()['id']))
        async with container.database.sessions() as session:
            hold = await session.get(CreditHold, UUID(response.json()['id']))
            assert hold.user_id == member_id and hold.charged_points == Decimal('0.0018')
            assert (await container.credits.summary(session, actor.id)).used_points == 0


async def test_rerank_search_units_settle_points_and_money_without_fabricating_tokens(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    connection_id = (await admin.get('/api/models')).json()[0]['connection_id']
    response = await admin.post(
        '/api/models',
        json={
            'name': 'Unknown rerank',
            'connection_id': connection_id,
            'upstream_model': 'rerank-no-usage',
            'model_type': 'rerank',
        },
    )
    assert response.status_code == 201, response.text
    model = response.json()['id']
    async with container.database.sessions.begin() as session:
        await container.credits.create_rate(
            session,
            actor,
            UUID(model),
            CreditRateCreate(
                billing_mode='search_unit',
                search_points='10',
                reason='Unknown rerank rate',
                client_request_id=uuid4().hex,
            ),
            model_type='rerank',
        )
    money_rate = await admin.post(
        f'/api/models/{model}/rate-cards', json={'billing_mode': 'search_unit', 'search_price': '0.25'}
    )
    assert money_rate.status_code == 201, money_rate.text
    key = await issue(admin, model, monthly_budget='0.5')
    response = await drive(
        container,
        asyncio.create_task(
            admin.post(
                '/v1/rerank',
                headers={'Authorization': 'Bearer ' + key['api_key']},
                json={'model': model, 'query': 'first', 'documents': ['first', 'second']},
            )
        ),
    )
    assert response.status_code == 200, response.text
    async with container.database.sessions() as session:
        hold = await session.scalar(select(CreditHold).where(CreditHold.model_id == UUID(model)))
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == hold.run_id))
        assert hold.status == 'settled' and hold.charged_points == Decimal('20')
        assert invocation.status == 'unknown' and invocation.search_units == 2
        assert invocation.input_tokens is None and invocation.output_tokens is None
        assert invocation.billable_cost == Decimal('0.50')
        account = await session.get(UsageAccount, actor.id)
        assert account.reserved_tokens == invocation.reserved_tokens and account.used_tokens == 0
        summary = await container.credits.summary(session, actor.id)
        assert summary.reserved_points == 0 and summary.used_points == Decimal('20')
    with pytest.raises(AppError) as limited:
        async with container.database.sessions.begin() as session:
            await container.public_runs.admit(
                session,
                key['api_key'],
                model_id=UUID(model),
                operation='rerank',
                messages=(ModelMessage('user', 'first'), ModelMessage('user', 'first'), ModelMessage('user', 'second')),
                options={},
                token_limit=1,
            )
    assert limited.value.code == 'rate_limit_exceeded'


async def test_points_mode_continues_bounded_calls_after_historical_counters_exceed_int32(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    minute = utcnow()
    monkeypatch.setattr('zhiheng.modules.developer.limits.utcnow', lambda: minute)
    actor = await prepare(admin, app, model_id)
    key = await issue(admin, model_id)
    container, first, second = app.state.container, uuid4(), uuid4()
    fields = {
        'user_id': actor.id,
        'model_id': UUID(model_id),
        'source': 'public',
        'tokens': 20,
        'token_limit': 1,
        'key_id': UUID(key['id']),
        'estimated_input_tokens': 10,
        'max_output_tokens': 10,
    }
    async with container.database.sessions.begin() as session:
        await container.usage.reserve(session, run_id=first, **fields)
        await container.usage.settle(session, first, input_tokens=8, output_tokens=2)
        account = await session.get(UsageAccount, actor.id)
        bucket = await session.scalar(select(KeyMinuteBucket).where(KeyMinuteBucket.key_id == UUID(key['id'])))
        # Seed historical aggregate counters; every newly executed call remains 8/2 Tokens.
        account.limit_tokens = 2**31 + 100
        account.used_tokens = account.reserved_tokens = bucket.tokens = 2**31 - 5
    async with container.database.sessions.begin() as session:
        await container.usage.reserve(session, run_id=second, **fields)
    async with container.database.sessions.begin() as session:
        account = await session.get(UsageAccount, actor.id)
        bucket = await session.scalar(select(KeyMinuteBucket).where(KeyMinuteBucket.key_id == UUID(key['id'])))
        assert account.reserved_tokens == bucket.tokens == 2**31 + 15
        await container.usage.settle(session, second, input_tokens=8, output_tokens=2)
    async with container.database.sessions() as session:
        account = await session.get(UsageAccount, actor.id)
        bucket = await session.scalar(select(KeyMinuteBucket).where(KeyMinuteBucket.key_id == UUID(key['id'])))
        assert account.limit_tokens == 2**31 + 100
        assert account.used_tokens == bucket.tokens == 2**31 + 5
        assert account.reserved_tokens == 2**31 - 5
        assert (await container.credits.summary(session, actor.id)).used_points == Decimal('0.0036')
