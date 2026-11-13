"""Billing HTTP ownership, role revocation and switching against real PostgreSQL."""

import asyncio
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, text

from tests.core.integration.test_ai_management_permissions import account
from zhiheng.application.billing.management import BillingManagementService
from zhiheng.shared.errors import AppError


def mutation(**fields: Any) -> dict[str, Any]:
    return {'reason': 'manual verification', 'client_request_id': uuid4().hex, **fields}


async def point_rate(admin: httpx.AsyncClient, model_id: str, **fields: Any) -> httpx.Response:
    response = await admin.post(
        f'/api/billing/models/{model_id}/rates',
        json=mutation(billing_mode='token', input_points='100', output_points='500', cache_points='25', **fields),
    )
    assert response.status_code == 201, response.text
    return response


async def test_me_owner_boundary_and_zero_account_directory(admin: httpx.AsyncClient, app: FastAPI) -> None:
    assert (await admin.get('/api/billing/state')).json() == {'enabled': False, 'version': 0}
    async with account(admin, app) as (member, _, user):
        own = await member.get('/api/billing/me', params={'user_id': str(uuid4())})
        assert own.status_code == 200, own.text
        assert own.json()['user_id'] == user['id'] and Decimal(own.json()['available_points']) == 0
        assert (await member.get('/api/billing/me/ledger')).json()['items'] == []
        assert (await member.get('/api/billing/me/usage')).json()['items'] == []
        assert (await member.get('/api/billing/accounts')).status_code == 403
        assert (await member.get(f'/api/billing/accounts/{uuid4()}/ledger')).status_code == 403
        assert (await member.get(f'/api/billing/accounts/{uuid4()}/usage')).status_code == 403
        directory = await admin.get('/api/billing/accounts', params={'search': user['username'], 'page_size': 1})
        assert directory.status_code == 200, directory.text
        assert directory.json()['total'] == 1
        item = directory.json()['items'][0]
        assert item['user_id'] == user['id'] and item['username'] == user['username']
        assert item['display_name'] == '' and item['is_active'] and Decimal(item['available_points']) == 0


async def test_credit_roles_load_from_catalog_and_revoke_existing_session(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    catalog = await admin.get('/api/admin/permissions')
    codes = [row['code'] for row in catalog.json()['items']]
    assert len(codes) == len(set(codes))
    assert not any(code.startswith(('system:posts:', 'system:menus:', 'system:dictionaries:')) for code in codes)
    assert {'ai:credits:read', 'ai:credits:write'} <= {item['code'] for item in catalog.json()['items']}
    all_permissions = [item['code'] for item in catalog.json()['items']]
    async with account(admin, app, *all_permissions) as (full_role, _, _):
        actual = (await full_role.get('/api/auth/me')).json()
        assert not actual['is_admin'] and set(actual['permissions']) == set(all_permissions)
    async with account(admin, app, 'ai:credits:read') as (reader, _, user):
        assert (await reader.get('/api/billing/accounts')).status_code == 200
        assert (await reader.get(f'/api/billing/accounts/{user["id"]}/ledger')).status_code == 200
        assert (await reader.get(f'/api/billing/accounts/{user["id"]}/usage')).status_code == 200
        assert (await reader.get('/api/admin/users')).status_code == 403
        assert (await reader.get('/api/admin/permissions')).status_code == 403
        assert (
            await reader.post(f'/api/billing/accounts/{user["id"]}/adjust', json=mutation(kind='grant', points='1'))
        ).status_code == 403
    async with account(admin, app, 'ai:credits:write') as (writer, role, user):
        body = mutation(kind='grant', points='10')
        path = f'/api/billing/accounts/{user["id"]}/adjust'
        first = await writer.post(path, json=body)
        repeated = await writer.post(path, json=body)
        assert first.status_code == repeated.status_code == 200, first.text
        assert first.json() == repeated.json()
        assert Decimal(first.json()['available_points']) == 10
        assert (await writer.post(path, json={**body, 'points': '11'})).status_code == 409
        assert (
            await writer.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0))
        ).status_code == 403
        revoked = await admin.patch(f'/api/admin/roles/{role["id"]}', json={'permissions': [], 'expected_version': 1})
        assert revoked.status_code == 200, revoked.text
        assert (await writer.get('/api/auth/me')).json()['permissions'] == []
        assert (await writer.get('/api/billing/accounts')).status_code == 403
        assert (await writer.post(path, json=body)).status_code == 403


async def test_target_status_validation_reclaim_pause_and_exact_input(admin: httpx.AsyncClient, app: FastAPI) -> None:
    async with account(admin, app) as (_member, _, user):
        identifier = user['id']
        path = f'/api/billing/accounts/{identifier}'
        grant = await admin.post(f'{path}/adjust', json=mutation(kind='grant', points='10'))
        assert grant.status_code == 200, grant.text
        pause = await admin.patch(
            f'{path}/state', json=mutation(enabled=False, expected_version=grant.json()['version'])
        )
        assert pause.status_code == 200 and not pause.json()['enabled']
        assert (
            await admin.patch(f'{path}/state', json=mutation(enabled=True, expected_version=grant.json()['version']))
        ).status_code == 409
        disabled = await admin.patch(f'/api/admin/users/{identifier}', json={'is_active': False, 'expected_version': 1})
        assert disabled.status_code == 200, disabled.text
        blocked = await admin.post(f'{path}/adjust', json=mutation(kind='grant', points='1'))
        assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'credit_user_inactive'
        reclaimed = await admin.post(f'{path}/adjust', json=mutation(kind='reclaim', points='3'))
        assert reclaimed.status_code == 200 and Decimal(reclaimed.json()['available_points']) == 7
        item = (await admin.get('/api/billing/accounts', params={'search': user['username']})).json()['items'][0]
        assert not item['is_active']
        assert (await admin.post(f'{path}/adjust', json=mutation(kind='grant', points=0.1))).status_code == 422
        assert (
            await admin.post(f'/api/billing/accounts/{uuid4()}/adjust', json=mutation(kind='grant', points='1'))
        ).status_code == 404
        assert (
            await admin.delete(
                f'/api/admin/users/{identifier}',
                params={'expected_version': disabled.json()['version']},
                headers={'content-type': 'application/json'},
            )
        ).status_code == 204
        assert (await admin.get('/api/billing/accounts', params={'search': user['username']})).json()['items'] == []


async def test_point_rate_history_quote_scope_and_model_type(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    created = await point_rate(admin, model_id)
    assert created.json()['version'] == 1
    async with account(admin, app) as (member, _, _):
        assert (await member.get(f'/api/billing/models/{model_id}/quote')).json() == created.json()
        assert (await member.get(f'/api/billing/models/{model_id}/rates')).status_code == 403
        assert (await admin.post(f'/api/models/{model_id}/disable', json={})).status_code == 200
        assert (await member.get(f'/api/billing/models/{model_id}/quote')).status_code == 404
    async with account(admin, app, 'ai:models:read') as (reader, _, _):
        assert (await reader.get(f'/api/billing/models/{model_id}/quote')).status_code == 200
        assert (await reader.get(f'/api/billing/models/{model_id}/rates')).status_code == 403
    async with account(admin, app, 'ai:rates:write') as (writer, _, _):
        assert (await writer.get(f'/api/billing/models/{model_id}/rates')).json() == [created.json()]
        invalid = await writer.post(
            f'/api/billing/models/{model_id}/rates', json=mutation(billing_mode='search_unit', search_points='1')
        )
        assert invalid.status_code == 422
        assert (await writer.get(f'/api/billing/models/{model_id}/quote')).status_code == 200
        assert (
            await writer.post(
                f'/api/billing/models/{model_id}/rates',
                json=mutation(billing_mode='token', input_points='1', model_type='rerank'),
            )
        ).status_code == 422


async def test_point_rate_optional_note_keeps_version_operator_audit_and_replay(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    operator_id = (await admin.get('/api/auth/me')).json()['id']
    path = f'/api/billing/models/{model_id}/rates'
    for version, note in enumerate((None, '', '   ', ' 调整积分倍率 '), start=1):
        body = {'billing_mode': 'token', 'input_points': '1', 'client_request_id': uuid4().hex}
        if note is not None:
            body['reason'] = note
        created = await admin.post(path, json=body)
        assert created.status_code == 201, created.text
        assert created.json()['version'] == version and created.json()['created_at']
        replay = await admin.post(path, json=body)
        assert replay.status_code == 201 and replay.json() == created.json()
        async with app.state.container.database.sessions() as session:
            command = (
                await session.execute(
                    text(
                        'SELECT operator_id, reason, created_at, result FROM billing_commands '
                        'WHERE client_request_id = :request_id'
                    ),
                    {'request_id': body['client_request_id']},
                )
            ).one()
            assert str(command.operator_id) == operator_id
            assert command.reason == (note or '').strip() and command.created_at
            assert command.result['version'] == version
    assert len((await admin.get(path)).json()) == 4


async def test_mode_switch_optional_note_keeps_audit_and_disabling_needs_no_prices(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    await point_rate(admin, model_id)
    enabled = {'enabled': True, 'expected_version': 0, 'client_request_id': uuid4().hex}
    result = await admin.patch('/api/billing/state', json=enabled)
    assert result.status_code == 200 and result.json() == {'enabled': True, 'version': 1}, result.text
    assert (await admin.patch('/api/billing/state', json=enabled)).json() == result.json()
    model = next(item for item in (await admin.get('/api/models')).json() if item['id'] == model_id)
    unpriced = await admin.post(
        '/api/models',
        json={
            'name': 'New unpriced model',
            'connection_id': model['connection_id'],
            'upstream_model': 'new-unpriced',
            'max_output_tokens': 64,
        },
    )
    assert unpriced.status_code == 201, unpriced.text
    disabled = {'enabled': False, 'expected_version': 1, 'client_request_id': uuid4().hex, 'reason': '  '}
    result = await admin.patch('/api/billing/state', json=disabled)
    assert result.status_code == 200 and result.json() == {'enabled': False, 'version': 2}, result.text
    operator_id = (await admin.get('/api/auth/me')).json()['id']
    async with app.state.container.database.sessions() as session:
        commands = (
            await session.execute(
                text("SELECT operator_id,reason,created_at FROM billing_commands WHERE kind = 'state'")
            )
        ).all()
        assert len(commands) == 2
        assert all(str(row.operator_id) == operator_id and row.reason == '' and row.created_at for row in commands)


async def test_mode_preflight_prices_pending_runtime_and_idempotent_replay(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    body = mutation(enabled=True, expected_version=0)
    missing = await admin.patch('/api/billing/state', json=body)
    assert missing.status_code == 409 and missing.json()['error']['code'] == 'credit_rates_incomplete'
    assert (await admin.get('/api/billing/state')).json() == {'enabled': False, 'version': 0}
    await point_rate(admin, model_id)
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    container = app.state.container
    async with container.database.sessions.begin() as session:
        run = await container.runs.create(
            session,
            owner_id=owner_id,
            resource_type='chat',
            resource_id=uuid4(),
            request_id=uuid4(),
            fingerprint='test',
            payload={},
        )
    busy = await admin.patch('/api/billing/state', json=body)
    assert busy.status_code == 409 and busy.json()['error']['code'] == 'billing_switch_busy'
    async with container.database.sessions.begin() as session:
        await container.runs.cancel(session, run.id, owner_id)
    enabled = await admin.patch('/api/billing/state', json=body)
    assert enabled.status_code == 200 and enabled.json() == {'enabled': True, 'version': 1}
    async with container.database.sessions.begin() as session:
        await container.runs.create(
            session,
            owner_id=owner_id,
            resource_type='chat',
            resource_id=uuid4(),
            request_id=uuid4(),
            fingerprint='test',
            payload={},
        )
    assert (await admin.patch('/api/billing/state', json=body)).json() == enabled.json()
    assert (
        await admin.patch('/api/billing/state', json=mutation(enabled=False, expected_version=1))
    ).status_code == 409


@pytest.mark.parametrize('kind', ['build', 'graph', 'retrieval'])
async def test_mode_preflight_includes_knowledge_jobs(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, kind: str
) -> None:
    from datetime import timedelta

    from zhiheng.modules.knowledge.graph.orm import GraphJob
    from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeJob, RetrievalIntent
    from zhiheng.shared.time import utcnow

    await point_rate(admin, model_id)
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    container = app.state.container
    async with container.database.sessions.begin() as session:
        base = KnowledgeBase(owner_id=owner_id, name='mode preparation', embed_model_id=UUID(model_id))
        session.add(base)
        await session.flush()
        if kind == 'build':
            job = KnowledgeJob(
                base_id=base.id, actor_id=owner_id, client_request_id=uuid4(), fingerprint='x', operation='build'
            )
        elif kind == 'graph':
            job = GraphJob(
                base_id=base.id,
                owner_id=owner_id,
                actor_id=owner_id,
                client_request_id=uuid4(),
                request_fingerprint='x',
                operation='build',
                attempt_id=uuid4(),
                frozen={},
                request={},
            )
        else:
            job = RetrievalIntent(
                namespace_id=uuid4(),
                client_request_id=uuid4(),
                actor_id=owner_id,
                owner_id=owner_id,
                resource_type='chat',
                resource_id=uuid4(),
                fingerprint='x',
                request={},
                dependencies={},
                deadline=utcnow() + timedelta(minutes=1),
            )
        session.add(job)
    response = await admin.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0))
    assert response.status_code == 409 and response.json()['error']['code'] == 'billing_switch_busy'


async def test_reconcile_http_releases_both_meters_and_records_safe_audit(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    await point_rate(admin, model_id)
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    assert (
        await admin.post(f'/api/billing/accounts/{owner_id}/adjust', json=mutation(kind='grant', points='100'))
    ).status_code == 200
    assert (await admin.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0))).status_code == 200
    container, run_id = app.state.container, uuid4()
    async with container.database.sessions.begin() as session:
        await container.usage.reserve(
            session,
            user_id=owner_id,
            run_id=run_id,
            model_id=UUID(model_id),
            source='test',
            tokens=2000,
            token_limit=10000,
            estimated_input_tokens=1000,
            max_output_tokens=1000,
        )
        await container.usage.settle(session, run_id, input_tokens=1000)
    row = (await admin.get('/api/billing/me/usage')).json()['items'][0]
    invalid = await admin.post(
        f'/api/billing/usage/{run_id}/reconcile',
        json=mutation(expected_version=row['version'], output_tokens=200, cache_tokens=1001),
    )
    assert invalid.status_code == 422, invalid.text
    assert (await admin.get('/api/billing/me/usage')).json()['items'][0] == row
    updated_price = await admin.post(
        f'/api/billing/models/{model_id}/rates',
        json=mutation(billing_mode='token', input_points='1000', output_points='5000', cache_points='250'),
    )
    assert updated_price.status_code == 201 and updated_price.json()['version'] == 2
    disabled = await admin.patch('/api/billing/state', json=mutation(enabled=False, expected_version=1))
    assert disabled.status_code == 200 and not disabled.json()['enabled']
    assert (await admin.get('/api/billing/me/usage')).json()['items'][0] == row
    body = mutation(
        expected_version=row['version'], input_tokens=1000, output_tokens=200, cache_tokens=0, search_units=0
    )
    async with account(admin, app) as (member, _, _):
        assert (await member.post(f'/api/billing/usage/{run_id}/reconcile', json=body)).status_code == 403
    result = await admin.post(f'/api/billing/usage/{run_id}/reconcile', json=body)
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'settled' and Decimal(result.json()['charged_points']) == Decimal('0.2')
    assert result.json()['input_tokens'] == 1000 and result.json()['output_tokens'] == 200
    assert result.json()['rate_version'] == 1 and result.json()['billing_mode'] == 'token'
    assert (await admin.post(f'/api/billing/usage/{run_id}/reconcile', json=body)).json() == result.json()
    assert Decimal((await admin.get('/api/billing/me')).json()['available_points']) == Decimal('99.8')
    legacy = (await admin.get('/api/usage')).json()
    assert legacy['reserved_tokens'] == 0 and legacy['used_tokens'] == 1200
    async with container.database.sessions() as session:
        logs = (
            await session.execute(text("SELECT action,message FROM operations_logs WHERE action LIKE 'billing.%'"))
        ).all()
        assert logs and any(row.action == 'billing.reconcile' for row in logs)
        assert all(body['reason'] not in row.message and body['client_request_id'] not in row.message for row in logs)


async def test_administration_audit_and_credit_write_commit_or_rollback_together(
    admin: httpx.AsyncClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = (await admin.get('/api/auth/me')).json()['id']
    operations = app.state.container.operations
    original = operations.record

    async def unavailable(*args: Any, **kwargs: Any) -> None:
        raise AppError('audit_unavailable', '审计暂时不可用', 503)

    monkeypatch.setattr(operations, 'record', unavailable)
    body = mutation(kind='grant', points='10')
    path = f'/api/billing/accounts/{owner_id}/adjust'
    assert (await admin.post(path, json=body)).status_code == 503
    assert Decimal((await admin.get('/api/billing/me')).json()['allocated_points']) == 0
    assert (await admin.get('/api/billing/me/ledger')).json()['items'] == []
    monkeypatch.setattr(operations, 'record', original)
    result = await admin.post(path, json=body)
    assert result.status_code == 200 and Decimal(result.json()['allocated_points']) == 10


async def test_search_only_reconciliation_preserves_unknown_token_counts(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    await point_rate(admin, model_id)
    connection_id = (await admin.get('/api/models/connections')).json()[0]['id']
    rerank = await admin.post(
        '/api/models',
        json={
            'name': 'Rerank points',
            'connection_id': connection_id,
            'upstream_model': 'local-rerank',
            'model_type': 'rerank',
            'max_output_tokens': 1,
        },
    )
    assert rerank.status_code == 201, rerank.text
    rerank_id = rerank.json()['id']
    rate = await admin.post(
        f'/api/billing/models/{rerank_id}/rates', json=mutation(billing_mode='search_unit', search_points='0.2')
    )
    assert rate.status_code == 201, rate.text
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    assert (
        await admin.post(f'/api/billing/accounts/{owner_id}/adjust', json=mutation(kind='grant', points='100'))
    ).status_code == 200
    assert (await admin.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0))).status_code == 200
    container, run_id = app.state.container, uuid4()
    async with container.database.sessions.begin() as session:
        await container.usage.reserve(
            session,
            user_id=owner_id,
            run_id=run_id,
            model_id=UUID(rerank_id),
            source='rerank',
            tokens=1000,
            token_limit=10000,
            operation='rerank',
            estimated_input_tokens=1000,
            estimated_search_units=3,
        )
        await container.usage.settle(session, run_id)
    unknown = (await admin.get('/api/billing/me/usage')).json()['items'][0]
    result = await admin.post(
        f'/api/billing/usage/{run_id}/reconcile', json=mutation(expected_version=unknown['version'], search_units=2)
    )
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'settled' and result.json()['billing_mode'] == 'search_unit'
    assert Decimal(result.json()['charged_points']) == Decimal('0.4')
    assert result.json()['input_tokens'] is None and result.json()['output_tokens'] is None


async def test_missing_reconciliation_run_returns_a_business_error(admin: httpx.AsyncClient) -> None:
    response = await admin.post(
        f'/api/billing/usage/{uuid4()}/reconcile', json=mutation(expected_version=1, input_tokens=0, output_tokens=0)
    )
    assert response.status_code == 409 and response.json()['error']['code'] == 'invocation_unavailable'


async def test_mode_switch_serializes_with_real_new_invocation_admission(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await point_rate(admin, model_id)
    owner_id = (await admin.get('/api/auth/me')).json()['id']
    assert (
        await admin.post(f'/api/billing/accounts/{owner_id}/adjust', json=mutation(kind='grant', points='100'))
    ).status_code == 200
    conversation = await admin.post('/api/chat/conversations', json={})
    assert conversation.status_code == 201, conversation.text
    checked, release, admission_attempt = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = BillingManagementService._before_switch

    async def pause_after_check(self: BillingManagementService, session: Any, enabled: bool) -> None:
        await original(self, session, enabled)
        checked.set()
        await release.wait()

    monkeypatch.setattr(BillingManagementService, '_before_switch', pause_after_check)

    def observe(connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
        if 'pg_advisory_xact_lock_shared' in statement or statement.startswith('INSERT INTO developer_invocations'):
            admission_attempt.set()

    engine = app.state.container.database.engine
    event.listen(engine.sync_engine, 'before_cursor_execute', observe)
    switching = asyncio.create_task(admin.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0)))
    admitting = None
    try:
        await asyncio.wait_for(checked.wait(), 10)
        admitting = asyncio.create_task(
            admin.post(
                f'/api/chat/conversations/{conversation.json()["id"]}/runs',
                json={'model_id': model_id, 'message': 'concurrent admission', 'client_request_id': str(uuid4())},
            )
        )
        await asyncio.wait_for(admission_attempt.wait(), 10)
        release.set()
        switched, admitted = await asyncio.wait_for(asyncio.gather(switching, admitting), 10)
        assert switched.status_code == 200 and admitted.status_code == 202, (switched.text, admitted.text)
        async with app.state.container.database.sessions() as session:
            assert (
                await session.scalar(
                    text('SELECT points_enforced FROM developer_invocations WHERE run_id=:id'),
                    {'id': UUID(admitted.json()['id'])},
                )
                is True
            )
            assert (
                await session.scalar(
                    text('SELECT count(*) FROM billing_reservations WHERE run_id=:id'),
                    {'id': UUID(admitted.json()['id'])},
                )
                == 1
            )
    finally:
        release.set()
        for task in (switching, admitting):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        event.remove(engine.sync_engine, 'before_cursor_execute', observe)


async def test_mode_switch_serializes_with_real_knowledge_parent_job_admission(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await point_rate(admin, model_id)
    connection_id = (await admin.get('/api/models/connections')).json()[0]['id']
    embedding = await admin.post(
        '/api/models',
        json={
            'name': 'Embedding parent',
            'connection_id': connection_id,
            'upstream_model': 'local-embedding',
            'model_type': 'embedding',
            'max_output_tokens': 1,
        },
    )
    assert embedding.status_code == 201, embedding.text
    await point_rate(admin, embedding.json()['id'])
    ranker = await admin.post(
        '/api/models',
        json={
            'name': 'Billing ranker',
            'connection_id': connection_id,
            'upstream_model': 'local-ranker',
            'model_type': 'rerank',
        },
    )
    assert ranker.status_code == 201, ranker.text
    price = await admin.post(
        '/api/billing/models/' + ranker.json()['id'] + '/rates',
        json={'billing_mode': 'search_unit', 'is_free': True, 'client_request_id': str(uuid4())},
    )
    assert price.status_code == 201, price.text
    current = (await admin.get('/api/knowledge/settings')).json()
    configured = await admin.patch(
        '/api/knowledge/settings',
        json={'expected_version': current['version'], 'default_rerank_model_id': ranker.json()['id']},
    )
    assert configured.status_code == 200, configured.text
    base = await admin.post(
        '/api/knowledge/bases', json={'name': 'Billing switch parent', 'embed_model_id': embedding.json()['id']}
    )
    assert base.status_code == 201, base.text
    base_id = base.json()['id']
    uploaded = await admin.post(
        f'/api/knowledge/bases/{base_id}/uploads?filename=parent.txt',
        content=b'local knowledge parent admission',
        headers={'content-type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    checked, release = asyncio.Event(), asyncio.Event()
    original = BillingManagementService._before_switch

    async def pause_after_check(self: BillingManagementService, session: Any, enabled: bool) -> None:
        await original(self, session, enabled)
        checked.set()
        await release.wait()

    monkeypatch.setattr(BillingManagementService, '_before_switch', pause_after_check)
    switching = asyncio.create_task(admin.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0)))
    admitting = None
    try:
        await asyncio.wait_for(checked.wait(), 10)
        admitting = asyncio.create_task(
            admin.post(
                f'/api/knowledge/bases/{base_id}/documents',
                json={'file_ids': [uploaded.json()['file']['id']], 'client_request_id': str(uuid4())},
            )
        )

        async def wait_for_real_shared_lock() -> None:
            async with app.state.container.database.sessions() as session:
                while True:
                    waiting = await session.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND mode='ShareLock' AND NOT granted"
                            ' AND database=(SELECT oid FROM pg_database WHERE datname=current_database())'
                            ' AND classid::bigint=((hashtextextended(:key,0)>>32)&4294967295)'
                            ' AND objid::bigint=(hashtextextended(:key,0)&4294967295) AND objsubid=1)'
                        ),
                        {'key': 'zhiheng:billing:mode'},
                    )
                    if waiting:
                        assert await session.scalar(text('SELECT count(*) FROM knowledge_jobs')) == 0
                        return
                    if admitting is not None and admitting.done():
                        raise AssertionError('Knowledge parent admission escaped the billing mode gate')
                    await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_real_shared_lock(), 10)
        release.set()
        changed, job = await asyncio.wait_for(asyncio.gather(switching, admitting), 10)
        assert changed.status_code == 200 and job.status_code == 202, (changed.text, job.text)
        assert job.json()['status'] == 'pending'
        assert (await admin.get('/api/billing/state')).json()['enabled']
    finally:
        release.set()
        for task in (switching, admitting):
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
