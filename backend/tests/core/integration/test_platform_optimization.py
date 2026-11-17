import httpx
import pytest
from fastapi import FastAPI

from tests.core.permission_support import account, grants
from tests.core.provider import ProviderServer


async def test_console_reuses_schema_check_and_projects_effective_capacity(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = app.state.container
    container.settings.chat_context_input_tokens = 2048
    calls = 0
    original = container.database.require_schema

    async def counted() -> None:
        nonlocal calls
        calls += 1
        await original()

    monkeypatch.setattr(container.database, 'require_schema', counted)
    result = await admin.get('/api/models')
    assert result.status_code == 200, result.text
    assert calls == 1
    row = next(x for x in result.json() if x['id'] == model_id)
    assert row['context_window_tokens'] is None
    assert row['chat_input_budget']['source'] == 'platform'
    assert row['chat_input_budget']['limit_tokens'] == 2048


async def test_conflicting_model_configuration_makes_no_provider_request(
    admin: httpx.AsyncClient, model_id: str, provider: ProviderServer
) -> None:
    before = len(provider.requests)
    rejected = await admin.patch(
        '/api/models/' + model_id, json={'context_window_tokens': 8192, 'max_output_tokens': 8192}
    )
    assert rejected.status_code == 422 and rejected.json()['error']['code'] == 'invalid_model_capacity'
    assert len(provider.requests) == before
    saved = await admin.patch(
        '/api/models/' + model_id, json={'context_window_tokens': 8192, 'max_output_tokens': 4096}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()['chat_input_budget']['limit_tokens'] == 3840


async def test_readonly_scope_catalog_tracks_current_role_and_matches_real_key_issuance(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    permissions = (
        grants('platform:developer', 'create', 'key_create') | grants('platform:knowledge') | grants('platform:skills')
    )
    async with account(admin, app, *permissions) as (reader, role, _user):
        path = '/api/developer/applications'
        catalog = (await reader.get(path + '/scope-catalog')).json()
        definitions = {x['scope']: x for x in catalog['definitions']}
        assert definitions['knowledge:read']['grantable'] and definitions['skills:read']['grantable']
        assert not definitions['knowledge:query']['grantable']
        application = await reader.post(path, json={'name': 'Read only'})
        assert application.status_code == 201, application.text
        endpoint = path + '/' + application.json()['id'] + '/keys'
        issued = await reader.post(
            endpoint, json={'name': 'Reader', 'model_ids': [], 'scopes': ['knowledge:read', 'skills:read']}
        )
        assert issued.status_code == 201, issued.text
        rejected = await reader.post(endpoint, json={'name': 'Query', 'model_ids': [], 'scopes': ['knowledge:query']})
        assert rejected.status_code == 403
        changed = await admin.patch(
            '/api/admin/roles/' + role['id'],
            json={
                'expected_version': role['version'],
                'permissions': sorted(permissions - {'platform:knowledge:enabled'}),
            },
        )
        assert changed.status_code == 200, changed.text
        refreshed = (await reader.get(path + '/scope-catalog')).json()
        assert not next(x for x in refreshed['definitions'] if x['scope'] == 'knowledge:read')['grantable']
        assert next(x for x in refreshed['definitions'] if x['scope'] == 'skills:read')['grantable']
