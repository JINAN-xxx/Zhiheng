"""Super-admin switches and quotas never grant access to personal applications or keys."""

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_ai_management_permissions import account

FIELDS = {
    'id',
    'name',
    'owner_id',
    'owner_enabled',
    'platform_enabled',
    'version',
    'source_file_limit',
    'source_byte_limit',
    'source_count',
    'source_bytes',
}


async def application(client: httpx.AsyncClient) -> dict:
    response = await client.post('/api/developer/applications', json={'name': 'Personal application'})
    assert response.status_code == 201, response.text
    return response.json()


async def test_governance_lists_minimal_metadata_and_keeps_personal_routes_private(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    async with account(admin, app) as (owner, _, user):
        owned = await application(owner)
        await application(admin)
        result = await admin.get('/api/admin/applications', params={'owner_id': user['id'], 'page_size': 1})
        assert result.status_code == 200, result.text
        assert result.json()['total'] == 1
        row = result.json()['items'][0]
        assert set(row) == FIELDS and row['id'] == owned['id']
        assert row['platform_enabled'] and row['owner_enabled']
        path = '/api/developer/applications/' + owned['id']
        for suffix in ['', '/keys', '/resource-access', '/resources']:
            assert (await admin.get(path + suffix)).status_code == 404
        assert (
            await admin.post(path + '/keys', json={'name': 'Forbidden', 'scopes': ['files:read']})
        ).status_code == 404
        assert (await admin.get('/api/developer/applications')).json()['total'] == 1


@pytest.mark.parametrize('permission', [None, 'system:users:read', 'system:users:write', 'ai:credits:write'])
async def test_governance_is_super_admin_only_even_for_the_application_owner(
    admin: httpx.AsyncClient, app: FastAPI, permission: str | None
) -> None:
    async with account(admin, app, *([permission] if permission else [])) as (owner, _, _):
        owned = await application(owner)
        base = '/api/admin/applications/' + owned['id']
        assert (await owner.get('/api/admin/applications')).status_code == 403
        assert (await owner.patch(base + '/state', json={'enabled': False, 'expected_version': 1})).status_code == 403
        assert (
            await owner.patch(
                base + '/resource-quota',
                json={'source_file_limit': 10, 'source_byte_limit': 4096, 'expected_version': 1},
            )
        ).status_code == 403
        current = (await owner.get('/api/developer/applications/' + owned['id'])).json()
        assert current['version'] == 1 and current['platform_enabled']


async def test_platform_suspend_fences_keys_and_cannot_be_reenabled_by_owner(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: object
) -> None:
    async with account(admin, app) as (owner, _, _):
        owned = await application(owner)
        path = '/api/developer/applications/' + owned['id']
        issued = await owner.post(path + '/keys', json={'name': 'Client', 'model_ids': [model_id]})
        assert issued.status_code == 201, issued.text
        secret = issued.json()['api_key']
        suspended = await admin.patch(
            '/api/admin/applications/' + owned['id'] + '/state', json={'enabled': False, 'expected_version': 1}
        )
        assert suspended.status_code == 200 and not suspended.json()['platform_enabled'], suspended.text
        assert secret not in suspended.text
        assert (await owner.post(path + '/enable', json={})).status_code == 409
        current = (await owner.get(path)).json()
        assert current['status'] == 'disabled' and not current['platform_enabled']
        assert (
            await owner.post(path + '/keys', json={'name': 'Cannot bypass', 'model_ids': [model_id]})
        ).status_code == 409
        rejected = await owner.post(
            '/v1/chat/completions',
            headers={'Authorization': 'Bearer ' + secret},
            json={'model': model_id, 'messages': [{'role': 'user', 'content': 'Must not execute'}]},
        )
        assert rejected.status_code == 401, rejected.text
        assert not provider.requests
        assert (await admin.get(path + '/keys/' + issued.json()['id'])).status_code == 404
        assert (await admin.post(path + '/keys/' + issued.json()['id'] + '/rotate', json={})).status_code == 404


async def test_platform_restore_preserves_the_owners_disabled_state_and_version(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    async with account(admin, app) as (owner, _, _):
        owned = await application(owner)
        path = '/api/developer/applications/' + owned['id']
        disabled = await owner.post(path + '/disable', json={})
        assert disabled.status_code == 200
        govern = '/api/admin/applications/' + owned['id'] + '/state'
        stopped = await admin.patch(govern, json={'enabled': False, 'expected_version': disabled.json()['version']})
        assert stopped.status_code == 200
        stale = await admin.patch(govern, json={'enabled': True, 'expected_version': disabled.json()['version']})
        assert stale.status_code == 409
        restored = await admin.patch(govern, json={'enabled': True, 'expected_version': stopped.json()['version']})
        assert restored.status_code == 200 and not restored.json()['owner_enabled']
        assert (await owner.get(path)).json()['status'] == 'disabled'
        assert (await owner.post(path + '/enable', json={})).status_code == 200


async def test_governance_quota_is_versioned_audited_and_does_not_open_personal_access(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    async with account(admin, app) as (owner, _, _):
        owned = await application(owner)
        base = '/api/admin/applications/' + owned['id']
        body = {'source_file_limit': 7, 'source_byte_limit': 4096, 'expected_version': 1}
        changed = await admin.patch(base + '/resource-quota', json=body)
        assert changed.status_code == 200 and changed.json()['version'] == 2
        assert (await admin.patch(base + '/resource-quota', json=body)).status_code == 409
        current = (await owner.get('/api/developer/applications/' + owned['id'])).json()
        assert current['source_file_limit'] == 7 and current['source_byte_limit'] == 4096
        assert (await admin.get('/api/developer/applications/' + owned['id'])).status_code == 404
        logs = (await admin.get('/api/admin/logs', params={'kind': 'operation'})).json()
        assert 'applications.resource_quota' in str(logs)


@pytest.mark.parametrize(
    'payload',
    [
        {'source_file_limit': 2147483648, 'source_byte_limit': 4096, 'expected_version': 1},
        {'source_file_limit': 10, 'source_byte_limit': 9007199254740992, 'expected_version': 1},
        {'source_file_limit': 10, 'source_byte_limit': 4096, 'expected_version': 1, 'owner_id': 'injected'},
    ],
)
async def test_governance_rejects_unsafe_or_extra_quota_fields(admin: httpx.AsyncClient, payload: dict) -> None:
    owned = await application(admin)
    response = await admin.patch('/api/admin/applications/' + owned['id'] + '/resource-quota', json=payload)
    assert response.status_code == 422, response.text


async def test_cross_site_governance_writes_are_rejected(admin: httpx.AsyncClient) -> None:
    owned = await application(admin)
    response = await admin.patch(
        '/api/admin/applications/' + owned['id'] + '/state',
        headers={'Origin': 'https://other.invalid'},
        json={'enabled': False, 'expected_version': 1},
    )
    assert response.status_code == 403
    current = (await admin.get('/api/developer/applications/' + owned['id'])).json()
    assert current['platform_enabled'] and current['version'] == 1


@pytest.mark.parametrize('metered', [False, True])
async def test_platform_suspend_stops_admitted_execution_without_consuming_quota(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: object, metered: bool
) -> None:
    from sqlalchemy import select

    from tests.core.billing_support import enable_free_billing
    from tests.core.integration.test_developer import admitted
    from zhiheng.modules.billing.orm import Invocation, UsageAccount
    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    if metered:
        await enable_free_billing(admin)
    async with account(admin, app) as (owner, _, _):
        owned = await application(owner)
        issued = await owner.post(
            '/api/developer/applications/' + owned['id'] + '/keys',
            json={'name': 'Admitted client', 'model_ids': [model_id]},
        )
        assert issued.status_code == 201, issued.text
        container = app.state.container
        run, identity = await admitted(container, issued.json()['api_key'], model_id)
        stopped = await admin.patch(
            '/api/admin/applications/' + owned['id'] + '/state',
            json={'enabled': False, 'expected_version': 1},
        )
        assert stopped.status_code == 200, stopped.text
        await execute_run(container, run.id)
        async with container.database.sessions() as session:
            persisted = await session.get(Run, run.id)
            invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run.id))
            wallet = await session.get(UsageAccount, identity.owner_id)
            assert persisted.status == 'failed' and not persisted.provider_started
            assert invocation.status == 'known' and invocation.charged_tokens == 0
            assert wallet.reserved_tokens == wallet.used_tokens == 0
        assert provider.requests == []
