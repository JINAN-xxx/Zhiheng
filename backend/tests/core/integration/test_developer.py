from uuid import UUID

import httpx
import pytest


@pytest.mark.asyncio
async def test_application_lifecycle_keys_and_owner_isolation(admin: httpx.AsyncClient, model_id: str) -> None:
    created = await admin.post('/api/developer/applications', json={'code': 'test-app', 'name': ' Demo '})
    assert created.status_code == 201, created.text
    app = created.json()
    assert app['name'] == 'Demo' and app['version'] == 1
    app_id = app['id']
    issued = await admin.post(
        f'/api/developer/applications/{app_id}/keys',
        json={'name': 'SDK', 'model_ids': [model_id], 'rpm_limit': 2, 'monthly_budget': '0'},
    )
    assert issued.status_code == 201, issued.text
    key = issued.json()
    UUID(key['id'])
    assert key['api_key'].startswith('sk-zh-')
    listed = (await admin.get(f'/api/developer/applications/{app_id}/keys')).json()
    assert listed['total'] == 1
    assert 'api_key' not in listed['items'][0] and 'digest' not in listed['items'][0]
    assert (
        await admin.patch(f'/api/developer/applications/{app_id}', json={'name': 'Next', 'expected_version': 9})
    ).status_code == 409
    assert (
        await admin.delete(f'/api/developer/applications/{app_id}', headers={'Content-Type': 'application/json'})
    ).status_code == 409
    assert (await admin.post(f'/api/developer/applications/{app_id}/disable', json={})).status_code == 200
    assert (
        await admin.post(
            '/v1/chat/completions',
            headers={'Authorization': f'Bearer {key["api_key"]}'},
            json={'model': model_id, 'messages': [{'role': 'user', 'content': 'hi'}]},
        )
    ).status_code == 401
    assert (
        await admin.delete(f'/api/developer/applications/{app_id}', headers={'Content-Type': 'application/json'})
    ).status_code == 200
    assert (
        await admin.post('/api/developer/applications', json={'code': 'test-app', 'name': 'Duplicate'})
    ).status_code == 409
    await admin.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
    assert (await admin.get(f'/api/developer/applications/{app_id}')).status_code == 404


@pytest.mark.asyncio
async def test_public_requires_independent_bearer_and_strict_fields(admin: httpx.AsyncClient) -> None:
    response = await admin.get('/v1/models')
    assert response.status_code == 401
    assert set(response.json()['error']) == {'message', 'type', 'param', 'code'}
    duplicate = await admin.get(
        '/v1/models', headers=[('Authorization', 'Bearer fake'), ('Authorization', 'Bearer fake')]
    )
    assert duplicate.status_code == 401


@pytest.mark.asyncio
async def test_rate_cards_freeze_public_reservations(admin: httpx.AsyncClient, model_id: str, app: object) -> None:
    import asyncio
    from decimal import Decimal

    from sqlalchemy import select

    from zhiheng.modules.developer.orm import Invocation
    from zhiheng.worker.execution import execute_run

    rate = await admin.post(
        f'/api/models/{model_id}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.02'},
    )
    assert rate.status_code == 201, rate.text
    application = (await admin.post('/api/developer/applications', json={'code': 'public', 'name': 'Public'})).json()
    key = (
        await admin.post(
            f'/api/developer/applications/{application["id"]}/keys',
            json={'name': 'SDK', 'model_ids': [model_id], 'rpm_limit': 1},
        )
    ).json()
    headers = {'Authorization': f'Bearer {key["api_key"]}'}
    visible = await admin.get('/v1/models', headers=headers)
    assert [row['id'] for row in visible.json()['data']] == [model_id]
    task = asyncio.create_task(
        admin.post(
            '/v1/chat/completions',
            headers=headers,
            json={'model': model_id, 'messages': [{'role': 'user', 'content': 'Hi'}]},
        )
    )
    container = app.state.container
    invocation = None
    for _ in range(100):
        async with container.database.sessions() as session:
            invocation = await session.scalar(
                select(Invocation).where(Invocation.application_id == UUID(application['id']))
            )
        if invocation is not None:
            break
        await asyncio.sleep(0.01)
    assert invocation is not None
    await admin.post(
        f'/api/models/{model_id}/rate-cards', json={'billing_mode': 'token', 'input_price': '9', 'output_price': '9'}
    )
    await execute_run(container, invocation.run_id)
    response = await task
    assert response.status_code == 200, response.text
    assert response.json()['choices'][0]['message']['content'] == '你好'
    assert response.json()['model'] == model_id
    usage = (await admin.get(f'/api/developer/applications/{application["id"]}/usage')).json()
    assert usage['total'] == 1
    assert usage['items'][0]['rate_version'] == 1
    assert Decimal(usage['items'][0]['billable_cost']) == Decimal('0.12')
    limited = await admin.post(
        '/v1/chat/completions',
        headers=headers,
        json={'model': model_id, 'messages': [{'role': 'user', 'content': 'Hi'}]},
    )
    assert limited.status_code == 429
    assert (await admin.get(f'/api/developer/applications/{application["id"]}/usage')).json()['total'] == 1


async def provision(admin: httpx.AsyncClient, model_id: str, **limits: object) -> tuple[str, dict[str, object]]:
    from uuid import uuid4

    rate = await admin.post(
        f'/api/models/{model_id}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.02'},
    )
    assert rate.status_code == 201, rate.text
    application = (
        await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'SDK app'})
    ).json()
    response = await admin.post(
        f'/api/developer/applications/{application["id"]}/keys', json={'name': 'SDK', 'model_ids': [model_id], **limits}
    )
    assert response.status_code == 201, response.text
    return application['id'], response.json()


async def admitted(container: object, secret: str, model_id: str) -> object:
    from zhiheng.modules.models.public import ModelMessage

    async with container.database.sessions.begin() as session:
        return await container.public_runs.admit(
            session,
            secret,
            model_id=UUID(model_id),
            operation='chat',
            messages=(ModelMessage('user', 'Hi'),),
            options={},
            token_limit=container.settings.token_limit,
        )


@pytest.mark.asyncio
async def test_rotation_zero_grace_and_owner_disable(admin: httpx.AsyncClient, model_id: str, app: object) -> None:
    from zhiheng.modules.identity.orm import IdentityUser

    application_id, key = await provision(admin, model_id)
    base = f'/api/developer/applications/{application_id}/keys/{key["id"]}'
    rotated = await admin.post(base + '/rotate', json={'grace_period_seconds': 0})
    assert rotated.status_code == 201, rotated.text
    assert rotated.json()['model_ids'] == [model_id]
    assert (await admin.post(base + '/rotate', json={})).status_code == 409
    assert (await admin.get('/v1/models', headers={'Authorization': f'Bearer {key["api_key"]}'})).status_code == 401
    headers = {'Authorization': f'Bearer {rotated.json()["api_key"]}'}
    assert (await admin.get('/v1/models', headers=headers)).status_code == 200
    async with app.state.container.database.sessions.begin() as session:
        from sqlalchemy import select

        owner = await session.scalar(select(IdentityUser).where(IdentityUser.username == 'admin'))
        owner.is_active = False
    assert (await admin.get('/v1/models', headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_atomic_admission_unknown_usage_and_recovery(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    import asyncio

    from sqlalchemy import func, select

    from zhiheng.modules.developer.orm import Invocation, UsageAccount
    from zhiheng.shared.errors import AppError
    from zhiheng.worker.execution import execute_run

    assert (await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'no-usage'})).status_code == 200
    application_id, key = await provision(admin, model_id, rpm_limit=1)
    container = app.state.container
    outcomes = await asyncio.gather(
        *(admitted(container, key['api_key'], model_id) for _ in range(6)), return_exceptions=True
    )
    successes = [result for result in outcomes if isinstance(result, tuple)]
    assert len(successes) == 1
    assert all(
        isinstance(result, tuple) or (isinstance(result, AppError) and result.status_code == 429) for result in outcomes
    )
    run, identity = successes[0]
    await execute_run(container, run.id)
    async with container.database.sessions.begin() as session:
        invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run.id))
        assert invocation.status == 'unknown' and invocation.billable_cost is None
        account = await session.get(UsageAccount, identity.owner_id)
        assert account.reserved_tokens == invocation.reserved_tokens and account.used_tokens == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(Invocation).where(Invocation.application_id == UUID(application_id))
            )
            == 1
        )
        again = await container.usage.settle(session, run.id, input_tokens=1, output_tokens=1)
        assert again.status == 'unknown'


@pytest.mark.asyncio
async def test_budget_zero_currency_and_input_validation(admin: httpx.AsyncClient, model_id: str, app: object) -> None:
    import pytest

    from zhiheng.shared.errors import AppError

    _, key = await provision(admin, model_id, monthly_budget='0')
    with pytest.raises(AppError) as denied:
        await admitted(app.state.container, key['api_key'], model_id)
    assert denied.value.status_code == 429
    headers = {'Authorization': f'Bearer {key["api_key"]}'}
    invalid = await admin.post(
        '/v1/chat/completions',
        headers=headers,
        json={'model': model_id, 'messages': [{'role': 'user', 'content': 'hi'}], 'tools': []},
    )
    assert invalid.status_code == 400
    oversized = await admin.post('/v1/chat/completions', headers=headers, content=b'x' * 1048577)
    assert oversized.status_code == 413
    _, eur = await provision(admin, model_id, currency='EUR')
    with pytest.raises(AppError) as mismatch:
        await admitted(app.state.container, eur['api_key'], model_id)
    assert mismatch.value.code == 'currency_mismatch'


@pytest.mark.asyncio
async def test_model_key_reference_guard_and_frozen_connection(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    from sqlalchemy import select

    from zhiheng.modules.models.orm import ConnectionRevision
    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    _, key = await provision(admin, model_id)
    run, _ = await admitted(app.state.container, key['api_key'], model_id)
    async with app.state.container.database.sessions() as session:
        row = await session.get(Run, run.id)
        revision_id = UUID(row.payload['binding']['connection_revision_id'])
        revision = await session.get(ConnectionRevision, revision_id)
        connection_id = revision.connection_id
    patched = await admin.patch(
        f'/api/models/connections/{connection_id}', json={'base_url': 'http://127.0.0.1:1/v1', 'api_key': 'changed'}
    )
    assert patched.status_code == 200
    await execute_run(app.state.container, run.id)
    async with app.state.container.database.sessions() as session:
        result = await session.get(Run, run.id)
        assert result.status == 'completed' and result.text == '你好'
        assert (
            len(
                list(
                    await session.scalars(
                        select(ConnectionRevision).where(ConnectionRevision.connection_id == connection_id)
                    )
                )
            )
            == 2
        )
    assert (
        await admin.delete(f'/api/models/{model_id}', headers={'Content-Type': 'application/json'})
    ).status_code == 409
    assert (await admin.post(f'/api/models/{model_id}/disable', json={})).status_code == 200
    assert (await admin.get('/v1/models', headers={'Authorization': f'Bearer {key["api_key"]}'})).json()['data'] == []


@pytest.mark.asyncio
async def test_model_with_grants_cannot_be_deleted_without_invocations(admin: httpx.AsyncClient, model_id: str) -> None:
    models = (await admin.get('/api/models')).json()
    copy = await admin.post(
        '/api/models',
        json={'name': 'Only grant', 'connection_id': models[0]['connection_id'], 'upstream_model': 'local-text'},
    )
    granted_id = copy.json()['id']
    from uuid import uuid4

    application = (await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'Grant'})).json()
    key = await admin.post(
        f'/api/developer/applications/{application["id"]}/keys', json={'name': 'Grant', 'model_ids': [granted_id]}
    )
    assert key.status_code == 201
    deleted = await admin.delete(f'/api/models/{granted_id}', headers={'Content-Type': 'application/json'})
    assert deleted.status_code == 409


@pytest.mark.asyncio
async def test_cached_tokens_use_frozen_cache_price(admin: httpx.AsyncClient, model_id: str, app: object) -> None:
    from decimal import Decimal

    from zhiheng.worker.execution import execute_run

    await admin.patch(f'/api/models/{model_id}', json={'upstream_model': 'cached'})
    application_id, key = await provision(admin, model_id)
    rate = await admin.post(
        f'/api/models/{model_id}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.02', 'cache_price': '0.001'},
    )
    assert rate.status_code == 201
    run, _ = await admitted(app.state.container, key['api_key'], model_id)
    await execute_run(app.state.container, run.id)
    usage = (await admin.get(f'/api/developer/applications/{application_id}/usage')).json()['items'][0]
    assert Decimal(usage['billable_cost']) == Decimal('0.084')
    assert usage['cache_tokens'] == 4


@pytest.mark.asyncio
async def test_owner_isolation_and_concurrent_cas(admin: httpx.AsyncClient) -> None:
    import asyncio
    from uuid import uuid4

    application = (await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'Owner'})).json()
    url = f'/api/developer/applications/{application["id"]}'
    results = await asyncio.gather(
        *(admin.patch(url, json={'name': name, 'expected_version': 1}) for name in ['First', 'Second'])
    )
    assert sorted(result.status_code for result in results) == [200, 409]
    await admin.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
    for path in [url, url + '/keys', url + '/usage']:
        assert (await admin.get(path)).status_code == 404
    assert (await admin.patch(url, json={'name': 'stolen', 'expected_version': 2})).status_code == 404
    assert (await admin.post(url + '/disable', json={})).status_code == 404
    assert (
        await admin.post(
            '/api/developer/applications', json={'code': uuid4().hex, 'name': 'X', 'owner_id': application['owner_id']}
        )
    ).status_code == 422
    assert (await admin.get('/api/developer/applications')).json()['total'] == 0


@pytest.mark.asyncio
async def test_key_validation_and_database_secret_boundary(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4

    from sqlalchemy import select

    from zhiheng.modules.developer.orm import DeveloperKey

    application = (
        await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'Validation'})
    ).json()
    endpoint = f'/api/developer/applications/{application["id"]}/keys'
    for changes in [
        {'model_ids': [model_id, model_id]},
        {'rpm_limit': True},
        {'tpm_limit': 0},
        {'concurrency_limit': 1.2},
        {'monthly_budget': 0.2},
        {'expires_at': '2099-01-01T00:00:00'},
        {'expires_at': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()},
    ]:
        result = await admin.post(endpoint, json={'name': 'SDK', 'model_ids': [model_id], **changes})
        assert result.status_code == 422, result.text
    key = (await admin.post(endpoint, json={'name': 'SDK', 'model_ids': [model_id]})).json()
    async with app.state.container.database.sessions() as session:
        row = await session.scalar(select(DeveloperKey).where(DeveloperKey.id == UUID(key['id'])))
        assert key['api_key'] not in repr(row)
        assert row.digest != key['api_key'] and len(row.digest) == 64
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    assert (await admin.get('/v1/models', headers={'Authorization': f'Bearer {key["api_key"]}'})).status_code == 401
    assert (await admin.get(endpoint + '/' + key['id'])).json()['status'] == 'expired'


@pytest.mark.asyncio
async def test_concurrency_release_and_original_minute_settlement(
    admin: httpx.AsyncClient, model_id: str, app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from zhiheng.modules.developer.orm import Invocation, KeyMinuteBucket
    from zhiheng.shared.errors import AppError
    from zhiheng.shared.time import utcnow
    from zhiheng.worker.execution import execute_run

    _, key = await provision(admin, model_id, concurrency_limit=1)
    container = app.state.container
    first, identity = await admitted(container, key['api_key'], model_id)
    with pytest.raises(AppError) as limited:
        await admitted(container, key['api_key'], model_id)
    assert limited.value.status_code == 429
    async with container.database.sessions.begin() as session:
        await container.runs.cancel(session, first.id, identity.owner_id)
        await container.usage.settle(session, first.id, rejected_before_execution=True)
    second, _ = await admitted(container, key['api_key'], model_id)
    future = utcnow() + timedelta(days=32, minutes=2)
    monkeypatch.setattr('zhiheng.modules.developer.limits.utcnow', lambda: future)
    await execute_run(container, second.id)
    third, _ = await admitted(container, key['api_key'], model_id)
    async with container.database.sessions.begin() as session:
        rows = list(
            await session.scalars(
                select(KeyMinuteBucket)
                .where(KeyMinuteBucket.key_id == UUID(key['id']))
                .order_by(KeyMinuteBucket.minute)
            )
        )
        assert len(rows) == 2
        assert rows[0].tokens == 10
        current = await session.scalar(select(Invocation).where(Invocation.run_id == third.id))
        assert rows[1].tokens == current.reserved_tokens
        assert current.quota_month == future.strftime('%Y-%m')
        await container.runs.cancel(session, third.id, identity.owner_id)
        await container.usage.settle(session, third.id, rejected_before_execution=True)


@pytest.mark.asyncio
async def test_disabled_connection_removes_model_from_callable_catalog(admin: httpx.AsyncClient, model_id: str) -> None:
    connection = (await admin.get('/api/models')).json()[0]['connection_id']
    assert (await admin.post(f'/api/models/connections/{connection}/disable', json={})).status_code == 200
    assert (await admin.get('/api/models')).json() == []
    all_models = (await admin.get('/api/models', params={'include_disabled': True})).json()
    assert [row['id'] for row in all_models] == [model_id]
