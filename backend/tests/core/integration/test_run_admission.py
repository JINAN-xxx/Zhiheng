"""Shared admission preserves real ownership and atomically rolls back paid facts."""

from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_billing_runtime import issue, prepare
from tests.core.provider import ProviderServer
from zhiheng.application.execution.admission import AdmissionIdentity
from zhiheng.modules.billing.orm import CreditHold, CreditLedger, Invocation, UsageAccount
from zhiheng.modules.billing.public import CreditAdjustment, CreditRateCreate, lock_billing_admission
from zhiheng.modules.developer.orm import KeyMinuteBucket
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.modules.models.public import ModelImage, ModelMessage, ModelOptions, ModelRequest
from zhiheng.runtime.orm import Outbox, Run
from zhiheng.shared.errors import AppError


async def test_failed_reservation_leaves_no_orphan_run_outbox_or_key_bucket_even_when_caught(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    actor = await prepare(admin, app, model_id, points='0')
    key = await issue(admin, model_id, rpm_limit=1)
    container = app.state.container
    request_id, resource_id = uuid4(), uuid4()
    async with container.database.sessions.begin() as session:
        await lock_billing_admission(session)
        binding = await container.models.bindings.resolve(session, UUID(model_id))
        request = ModelRequest(binding=binding, messages=(ModelMessage('user', 'hello'),))
        user = await session.get(IdentityUser, actor.id)
        user.display_name = 'An unrelated outer change survives'
        fields = {
            'identity': AdmissionIdentity(owner_id=actor.id, billing_owner_id=actor.id, key_id=UUID(key['id'])),
            'resource_type': 'public',
            'resource_id': resource_id,
            'client_request_id': request_id,
            'fingerprint': 'same-logical-request',
            'request': request,
        }
        with pytest.raises(AppError) as rejected:
            await container.run_admission.reserve(session, **fields)
        assert rejected.value.code == 'credits_insufficient'
        for table in [Run, Outbox, Invocation, CreditHold, KeyMinuteBucket]:
            assert await session.scalar(select(func.count()).select_from(table)) == 0
        assert (
            await session.scalar(select(func.count()).select_from(CreditLedger).where(CreditLedger.kind == 'reserve'))
            == 0
        )
        # Funding and retrying within the same outer transaction consumes the
        # original request identity and Key RPM slot exactly once.
        await container.credits.adjust(
            session,
            actor,
            actor.id,
            CreditAdjustment(kind='grant', points='1', reason='Fund retry', client_request_id=uuid4().hex),
        )
        run = await container.run_admission.reserve(session, **fields)
        for table in [Run, Outbox, Invocation, CreditHold]:
            assert await session.scalar(select(func.count()).select_from(table)) == 1
        assert await session.scalar(select(func.sum(KeyMinuteBucket.requests))) == 1
    async with container.database.sessions.begin() as session:
        assert (await session.get(IdentityUser, actor.id)).display_name == 'An unrelated outer change survives'
        cancelled, release = await container.runs.cancel(session, run.id, actor.id)
        assert release and cancelled.status == 'cancelled'
        await container.usage.settle(session, run.id, rejected_before_execution=True)
        assert (await container.credits.queries.summary(session, actor.id)).reserved_points == 0
        assert (await session.get(UsageAccount, actor.id)).reserved_tokens == 0
    assert not provider.requests


async def test_actor_billing_owner_and_original_key_remain_distinct(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    key = await issue(admin, model_id)
    container = app.state.container
    async with container.database.sessions.begin() as session:
        await lock_billing_admission(session)
        member = await session.scalar(select(IdentityUser).where(IdentityUser.username == 'member'))
        binding = await container.models.bindings.resolve(session, UUID(model_id))
        snapshot = await container.run_admission.reserve(
            session,
            identity=AdmissionIdentity(owner_id=member.id, billing_owner_id=actor.id, key_id=UUID(key['id'])),
            resource_type='work_turn',
            resource_id=uuid4(),
            client_request_id=uuid4(),
            fingerprint='delegated',
            request=ModelRequest(binding=binding, messages=(ModelMessage('user', 'hello'),)),
        )
        run = await session.get(Run, snapshot.id)
        hold = await session.get(CreditHold, snapshot.id)
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == snapshot.id))
        assert run.owner_id == member.id and run.billing_owner_id == actor.id
        assert hold.user_id == invocation.user_id == actor.id
        assert hold.key_id == invocation.key_id == UUID(key['id'])
        assert invocation.application_id == UUID(key['application_id'])
        assert (await container.credits.queries.summary(session, member.id)).reserved_points == 0
        assert (await container.credits.queries.summary(session, actor.id)).reserved_points == hold.reserved_points > 0


@pytest.mark.parametrize('history_estimate', [None, 9000])
async def test_default_output_history_and_images_reserve_the_actual_frozen_context(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, history_estimate: int | None
) -> None:
    actor = await prepare(admin, app, model_id)
    changed = await admin.patch('/api/models/' + model_id, json={'max_output_tokens': 8192, 'vision_enabled': True})
    assert changed.status_code == 200, changed.text
    container = app.state.container
    async with container.database.sessions.begin() as session:
        await lock_billing_admission(session)
        binding = await container.models.bindings.resolve(session, UUID(model_id))
        binding = binding.model_copy(update={'options': ModelOptions(provider_output_default=True)})
        request = ModelRequest(
            binding=binding, messages=(ModelMessage('user', '汉', (ModelImage('data:image/jpeg;base64,AAAA'),)),)
        )
        run = await container.run_admission.reserve(
            session,
            identity=AdmissionIdentity(owner_id=actor.id, billing_owner_id=actor.id),
            resource_type='chat',
            resource_id=uuid4(),
            client_request_id=uuid4(),
            fingerprint='history-images',
            request=request,
            input_token_estimate=history_estimate,
        )
        hold = await session.get(CreditHold, run.id)
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run.id))
        expected = history_estimate or 2067  # Three UTF-8 bytes + message overhead + managed image cost.
        assert hold.request_payload['estimated_input_tokens'] == expected
        assert hold.request_payload['max_output_tokens'] == 4096
        assert invocation.reserved_tokens == expected + 4096
        assert hold.reserved_points == (Decimal(expected) * 100 + 4096 * 500) / 1000000


async def test_outer_transaction_rollback_removes_run_outbox_and_reservations(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    with pytest.raises(RuntimeError, match='outer rollback'):
        async with container.database.sessions.begin() as session:
            await lock_billing_admission(session)
            binding = await container.models.bindings.resolve(session, UUID(model_id))
            await container.run_admission.reserve(
                session,
                identity=AdmissionIdentity(owner_id=actor.id, billing_owner_id=actor.id),
                resource_type='connection_test',
                resource_id=uuid4(),
                client_request_id=uuid4(),
                fingerprint='outer',
                request=ModelRequest(binding=binding, messages=(ModelMessage('user', 'hello'),)),
            )
            raise RuntimeError('outer rollback')
    async with container.database.sessions() as session:
        for table in [Run, Outbox, Invocation, CreditHold]:
            assert await session.scalar(select(func.count()).select_from(table)) == 0
        assert (await container.credits.queries.summary(session, actor.id)).reserved_points == 0


async def test_explicit_input_cap_rejects_before_any_execution_or_billing_fact(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container
    async with container.database.sessions.begin() as session:
        binding = await container.models.bindings.resolve(session, UUID(model_id))
        with pytest.raises(AppError) as rejected:
            await container.run_admission.reserve(
                session,
                identity=AdmissionIdentity(owner_id=actor.id, billing_owner_id=actor.id),
                resource_type='connection_test',
                resource_id=uuid4(),
                client_request_id=uuid4(),
                fingerprint='cap',
                request=ModelRequest(binding=binding, messages=(ModelMessage('user', 'hello'),)),
                input_token_limit=20,
            )
        assert rejected.value.code == 'context_too_long'
        for table in [Run, Outbox, Invocation, CreditHold]:
            assert await session.scalar(select(func.count()).select_from(table)) == 0


async def test_rate_lookup_failure_is_not_misreported_as_missing_price_and_leaves_no_run(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = await prepare(admin, app, model_id)
    container = app.state.container

    async def fail_rate(*_args: object) -> None:
        raise AppError('rate_read_unavailable', 'Rate lookup unavailable', 503)

    monkeypatch.setattr(container.models.rates, 'latest', fail_rate)
    async with container.database.sessions.begin() as session:
        binding = await container.models.bindings.resolve(session, UUID(model_id))
        with pytest.raises(AppError) as rejected:
            await container.run_admission.reserve(
                session,
                identity=AdmissionIdentity(owner_id=actor.id, billing_owner_id=actor.id),
                resource_type='knowledge_graph',
                resource_id=uuid4(),
                client_request_id=uuid4(),
                fingerprint='rate',
                request=ModelRequest(binding=binding, messages=(ModelMessage('user', 'hello'),)),
            )
        assert rejected.value.code == 'rate_read_unavailable'
        for table in [Run, Outbox, Invocation, CreditHold]:
            assert await session.scalar(select(func.count()).select_from(table)) == 0


async def test_graph_empty_rerank_keeps_zero_units_instead_of_the_public_minimum(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    actor = await prepare(admin, app, model_id)
    connection_id = (await admin.get('/api/models')).json()[0]['connection_id']
    created = await admin.post(
        '/api/models',
        json={'name': 'Ranker', 'connection_id': connection_id, 'upstream_model': 'rerank', 'model_type': 'rerank'},
    )
    assert created.status_code == 201, created.text
    ranker_id = UUID(created.json()['id'])
    container = app.state.container
    async with container.database.sessions.begin() as session:
        await container.credits.rates.create(
            session,
            actor,
            ranker_id,
            CreditRateCreate(
                billing_mode='search_unit', search_points='10', reason='Ranker estimate', client_request_id=uuid4().hex
            ),
            model_type='rerank',
        )
        binding = await container.models.bindings.resolve(session, ranker_id)
        request = ModelRequest(binding=binding, messages=(ModelMessage('user', 'query'),))
        run = await container.run_admission.reserve(
            session,
            identity=AdmissionIdentity(owner_id=actor.id, billing_owner_id=actor.id),
            resource_type='knowledge_graph',
            resource_id=uuid4(),
            client_request_id=uuid4(),
            fingerprint='empty-rerank',
            request=request,
            search_unit_estimate=0,
        )
        hold = await session.get(CreditHold, run.id)
        assert hold.operation == 'rerank' and hold.reserved_points == 0
        assert hold.request_payload['estimated_search_units'] == 0
        assert hold.request_payload['max_output_tokens'] == 0
    assert not provider.requests
