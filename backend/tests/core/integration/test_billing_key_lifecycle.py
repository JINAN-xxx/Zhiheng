"""Key policy consumes billing facts while retaining original admission identity and money holds."""

from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.billing_support import enable_free_billing
from tests.core.integration.test_developer import admitted, provision
from tests.core.knowledge_support import knowledge_models
from zhiheng.modules.billing.orm import Invocation, UsageAccount
from zhiheng.modules.billing.public import InvocationIdentity, KeyUsageFacts
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError


async def test_read_only_key_facts_preserve_unknown_holds_and_original_month(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    await enable_free_billing(admin)
    _, key = await provision(admin, model_id)
    container = app.state.container
    run, identity = await admitted(container, key['api_key'], model_id)
    async with container.database.sessions.begin() as session:
        invoice = await session.scalar(select(Invocation).where(Invocation.run_id == run.id))
        original_cost, month = invoice.reserved_cost, invoice.quota_month
        original_tokens = invoice.reserved_tokens
        assert original_cost > 0
        before = await container.usage_facts.key_usage(session, identity.key_id, month=month, currency='USD')
        assert before == KeyUsageFacts(active=1, spent=original_cost)
        assert await container.usage_facts.invocation_identity(session, run.id) == InvocationIdentity(
            identity.application_id,
            identity.key_id,
            identity.owner_id,
            UUID(model_id),
        )
        missing = await container.usage.settle(session, run.id)
        assert missing.status == 'unknown' and missing.input_tokens is missing.output_tokens is None
        account = await session.get(UsageAccount, identity.owner_id)
        assert account.reserved_tokens == original_tokens and account.used_tokens == 0
        stored_run = await session.get(Run, run.id)
        stored_run.status = 'failed'
        await session.flush()
        assert await container.usage_facts.key_usage(
            session,
            identity.key_id,
            month=month,
            currency='USD',
        ) == KeyUsageFacts(active=0, spent=original_cost)
        assert (
            await container.usage_facts.key_usage(
                session,
                identity.key_id,
                month='1900-01',
                currency='USD',
            )
        ).spent == 0
        assert (
            await container.usage_facts.key_usage(
                session,
                identity.key_id,
                month=month,
                currency='EUR',
            )
        ).spent == 0
        await container.metering.reconcile(session, run.id, input_tokens=2, output_tokens=3)
        assert account.reserved_tokens == 0 and account.used_tokens == 5
        assert await container.usage_facts.key_usage(
            session,
            identity.key_id,
            month=month,
            currency='USD',
        ) == KeyUsageFacts(active=0, spent=Decimal('0.08'))


async def test_rotation_cannot_replace_the_original_meter_identity_for_execution(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    application_id, key = await provision(admin, model_id)
    container = app.state.container
    run, identity = await admitted(container, key['api_key'], model_id)
    response = await admin.post(
        f'/api/developer/applications/{application_id}/keys/{key["id"]}/rotate',
        json={'grace_period_seconds': 0},
    )
    assert response.status_code == 201, response.text
    assert response.json()['id'] != key['id']
    async with container.database.sessions.begin() as session:
        persisted = await container.usage_facts.invocation_identity(session, run.id)
        assert persisted.key_id == identity.key_id
        with pytest.raises(AppError) as rejected:
            await container.keys.authorize_run(session, run.id, scope='models:invoke')
        assert rejected.value.code == 'invalid_api_key'
        # The original reservation still settles after the Key is revoked.
        await container.usage.settle(session, run.id, rejected_before_execution=True)
        account = await session.get(UsageAccount, identity.owner_id)
        assert account.reserved_tokens == 0 and account.used_tokens == 0


async def test_paid_resource_knowledge_replay_keeps_original_key_and_both_ledgers(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: object,
) -> None:
    from uuid import uuid4

    from tests.core.integration.test_billing_http import mutation, point_rate
    from tests.core.integration.test_resource_files import auth, post
    from tests.core.integration.test_resource_key_scopes import issue
    from tests.core.integration.test_resource_knowledge import SCOPES, finish, setup
    from zhiheng.modules.billing.orm import CreditHold
    from zhiheng.modules.developer.orm import KeyMinuteBucket

    owner_app, key, base = await setup(admin, provider, app)
    model = base['embed_model_id']
    await point_rate(admin, model)
    ranker = (await knowledge_models(admin, model))[-1]
    rate = await admin.post(
        '/api/billing/models/' + ranker + '/rates',
        json={'billing_mode': 'search_unit', 'is_free': True, 'search_points': '0', 'client_request_id': str(uuid4())},
    )
    assert rate.status_code == 201, rate.text
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    grant = await admin.post(f'/api/billing/accounts/{owner}/adjust', json=mutation(kind='grant', points='10'))
    assert grant.status_code == 200, grant.text
    enabled = await admin.patch('/api/billing/state', json=mutation(enabled=True, expected_version=0))
    assert enabled.status_code == 200, enabled.text
    uploaded = await post(admin, key)
    external = uuid4()
    endpoint = f'/api/v1/knowledge-bases/{base["id"]}/documents'
    body = {'file_ids': [uploaded.json()['id']]}
    accepted = await admin.post(endpoint, headers=auth(key, external), json=body)
    assert accepted.status_code == 202, accepted.text
    operation = await finish(admin, app, key, accepted.json())
    assert operation['status'] == 'succeeded', operation
    container = app.state.container
    async with container.database.sessions() as session:
        before = await container.credits.summary(session, owner)
        assert before.used_points > 0 and before.reserved_points == 0
        invoices = list(await session.scalars(select(Invocation)))
        holds = list(await session.scalars(select(CreditHold)))
        assert len(invoices) == len(holds) == 1
        assert invoices[0].key_id == holds[0].key_id == UUID(key['id'])
    replay_key = await issue(
        admin,
        owner_app['id'],
        scopes=SCOPES,
        model_ids=[model],
        monthly_points_limit='0',
    )
    disabled = await admin.patch(f'/api/models/{model}', json={'enabled': False})
    assert disabled.status_code == 200, disabled.text
    replayed = await admin.post(endpoint, headers=auth(replay_key, external), json=body)
    assert replayed.status_code == 202 and replayed.json()['id'] == operation['id'], replayed.text
    async with container.database.sessions() as session:
        assert await container.credits.summary(session, owner) == before
        assert len(list(await session.scalars(select(Invocation)))) == 1
        assert len(list(await session.scalars(select(CreditHold)))) == 1
        assert (
            list(
                await session.scalars(
                    select(KeyMinuteBucket).where(KeyMinuteBucket.key_id == UUID(replay_key['id'])),
                )
            )
            == []
        )
