import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_knowledge import embedding_model
from tests.core.knowledge_support import share_library
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
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await embedding_model(admin, provider)
    base = await admin.post('/api/knowledge/bases', json={'name': 'Shared read only', 'embed_model_id': model})
    assert base.status_code == 201, base.text
    base_id = base.json()['id']
    permissions = (
        grants('platform:developer', 'create', 'key_create', 'grant')
        | grants('platform:knowledge')
        | grants('platform:skills')
    )
    async with account(admin, app, *permissions) as (reader, role, _user):
        path = '/api/developer/applications'
        catalog = (await reader.get(path + '/scope-catalog')).json()
        definitions = {x['scope']: x for x in catalog['definitions']}
        assert definitions['knowledge:read']['grantable'] and definitions['skills:read']['grantable']
        assert not definitions['knowledge:query']['grantable']
        application = await reader.post(path, json={'name': 'Read only'})
        assert application.status_code == 201, application.text
        await share_library(admin, reader, base_id)
        bound = await reader.put(
            f'{path}/{application.json()["id"]}/resources/knowledge_base/{base_id}',
            json={'expected_version': application.json()['version'], 'actions': ['read']},
        )
        assert bound.status_code == 200, bound.text
        endpoint = path + '/' + application.json()['id'] + '/keys'
        issued = await reader.post(
            endpoint,
            json={
                'name': 'Reader',
                'scopes': ['knowledge:read'],
                'resource_grants': [
                    {'kind': 'knowledge_base', 'resource_id': base_id, 'actions': ['read']},
                ],
            },
        )
        assert issued.status_code == 201, issued.text
        rejected = await reader.post(
            endpoint,
            json={
                'name': 'Query',
                'scopes': ['knowledge:query'],
                'resource_grants': [
                    {'kind': 'knowledge_base', 'resource_id': base_id, 'actions': ['query']},
                ],
            },
        )
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
