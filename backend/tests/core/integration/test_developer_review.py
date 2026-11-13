"""Persistent regressions for independent developer API review findings."""

import asyncio
from uuid import UUID

import httpx
import pytest

from tests.core.integration.test_developer import admitted, provision


@pytest.mark.asyncio
@pytest.mark.parametrize('grace,expected', [(0, 'revoked'), (60, 'retiring')])
async def test_rotation_state_and_revoke_clear_retirement(
    admin: httpx.AsyncClient, model_id: str, grace: int, expected: str
) -> None:
    app_id, key = await provision(admin, model_id)
    url = f'/api/developer/applications/{app_id}/keys/{key["id"]}'
    response = await admin.post(url + '/rotate', json={'grace_period_seconds': grace})
    assert response.status_code == 201, response.text
    state = (await admin.get(url)).json()
    assert state['status'] == expected
    assert (state['retire_at'] is None) == (grace == 0)
    assert (await admin.post(url + '/rotate', json={})).status_code == 409
    revoked = await admin.post(url + '/revoke', json={})
    assert revoked.status_code == 200
    assert revoked.json()['status'] == 'revoked' and revoked.json()['retire_at'] is None
    assert (await admin.post(url + '/revoke', json={})).json() == revoked.json()


@pytest.mark.asyncio
async def test_connection_deletion_keeps_frozen_run_reference_after_rebinding(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    from zhiheng.worker.execution import execute_run

    _, key = await provision(admin, model_id)
    run, _ = await admitted(app.state.container, key['api_key'], model_id)
    connection_a = (await admin.get('/api/models')).json()[0]['connection_id']
    replacement = await admin.post(
        '/api/models/connections',
        json={
            'name': 'Replacement',
            'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
            'api_key': 'replacement',
        },
    )
    assert replacement.status_code == 201
    updated = await admin.patch('/api/models/' + model_id, json={'connection_id': replacement.json()['id']})
    assert updated.status_code == 200
    deletion = await admin.delete(
        '/api/models/connections/' + connection_a, headers={'Content-Type': 'application/json'}
    )
    assert deletion.status_code == 409, deletion.text
    await execute_run(app.state.container, run.id)
    assert provider.request_headers[-1]['authorization'] == 'Bearer local-test-key'
    # Tombstoning releases the directory entry while retaining immutable credentials.
    assert (
        await admin.delete('/api/models/connections/' + connection_a, headers={'Content-Type': 'application/json'})
    ).status_code == 200


@pytest.mark.asyncio
async def test_connection_delete_serializes_with_admission(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from zhiheng.modules.models.public import ModelMessage

    _, key = await provision(admin, model_id)
    connection_a = (await admin.get('/api/models')).json()[0]['connection_id']
    container = app.state.container
    async with container.database.sessions.begin() as session:
        await container.public_runs.admit(
            session,
            key['api_key'],
            model_id=UUID(model_id),
            operation='chat',
            messages=(ModelMessage('user', 'Hi'),),
            options={},
            token_limit=container.settings.token_limit,
        )
        deletion = asyncio.create_task(
            admin.delete('/api/models/connections/' + connection_a, headers={'Content-Type': 'application/json'})
        )
        await asyncio.sleep(0.1)
        assert not deletion.done(), 'Deletion must wait for the admission connection lock'
    response = await asyncio.wait_for(deletion, timeout=2)
    assert response.status_code == 409, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'action',
    [
        'app/enable',
        'app/disable',
        'key/revoke',
        'model/enable',
        'model/disable',
        'model/publish',
        'model/unpublish',
        'connection/enable',
        'connection/disable',
    ],
)
async def test_empty_actions_reject_unknown_fields_before_mutation(
    admin: httpx.AsyncClient, model_id: str, app: object, action: str
) -> None:
    from sqlalchemy import func, select

    from zhiheng.modules.models.orm import ConnectionRevision

    app_id, key = await provision(admin, model_id)
    connection_id = (await admin.get('/api/models')).json()[0]['connection_id']
    target, verb = action.split('/')
    base = {
        'app': f'/api/developer/applications/{app_id}',
        'key': f'/api/developer/applications/{app_id}/keys/{key["id"]}',
        'model': f'/api/models/{model_id}',
        'connection': f'/api/models/connections/{connection_id}',
    }[target]

    async def snapshot() -> tuple[object, ...]:
        async with app.state.container.database.sessions() as session:
            revisions = await session.scalar(select(func.count()).select_from(ConnectionRevision))
        return (
            (await admin.get(f'/api/developer/applications/{app_id}')).json(),
            (await admin.get(f'/api/developer/applications/{app_id}/keys/{key["id"]}')).json(),
            (await admin.get('/api/models', params={'include_disabled': True})).json(),
            (await admin.get('/api/models/connections')).json(),
            revisions,
        )

    before = await snapshot()
    response = await admin.post(base + '/' + verb, json={'unexpected': 1})
    assert response.status_code == 422, response.text
    assert await snapshot() == before
    # Preserve the existing no-body form when JSON metadata is present.
    accepted = await admin.post(base + '/' + verb, headers={'Content-Type': 'application/json'})
    if action in {'model/publish', 'model/unpublish'}:
        assert accepted.status_code == 422, accepted.text
        assert await snapshot() == before
    else:
        assert accepted.status_code in {200, 409}, accepted.text
