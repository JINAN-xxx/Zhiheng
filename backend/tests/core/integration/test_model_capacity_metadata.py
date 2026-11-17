"""Capacity declarations are local configuration, not supplier health probes."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from sqlalchemy import text

from tests.core.integration.test_model_save_validation import configuration
from tests.core.permission_support import account, grants
from tests.core.provider import ProviderServer
from zhiheng.modules.models.orm import ModelDefinition
from zhiheng.shared.errors import AppError


async def saved_model(admin: httpx.AsyncClient, provider: ProviderServer) -> dict:
    body = await configuration(admin, provider)
    response = await admin.post('/api/models', json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def test_local_evidence_and_capacity_update_needs_no_credentials_or_supplier(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = await saved_model(admin, provider)
    assert (await admin.post(f'/api/models/connections/{model["connection_id"]}/disable', json={})).status_code == 200
    provider.catalog_requests.clear()
    provider.catalog_status = 500

    def forbidden_decrypt(_value: str) -> str:
        raise AssertionError('Local metadata must not decrypt provider credentials')

    monkeypatch.setattr(app.state.container.cipher, 'decrypt', forbidden_decrypt)
    async with account(admin, app, *grants('platform:models', 'update', 'config_read')) as (editor, _, _):
        assert (await editor.get('/api/models/connections')).status_code == 403
        response = await editor.patch(
            '/api/models/' + model['id'],
            json={
                'name': '  Evidence model  ',
                'context_window_tokens': 1000000,
                'max_output_tokens': 131072,
                'capacity_note': '  https://provider.example/spec v1  ',
            },
        )
    assert response.status_code == 200, response.text
    current = response.json()
    assert current['name'] == 'Evidence model' and current['capacity_note'] == 'https://provider.example/spec v1'
    assert current['capacity_status'] == 'documented'
    assert current['availability'] == {'available': False, 'reason': 'connection_disabled'}
    assert not provider.catalog_requests and not provider.requests
    async with app.state.container.database.sessions.begin() as session:
        with pytest.raises(AppError) as denied:
            await app.state.container.models.bindings.resolve(session, UUID(model['id']))
        assert denied.value.code == 'model_unavailable'


async def test_local_edit_keeps_cas_and_cannot_overwrite_a_concurrent_model(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await saved_model(admin, provider)
    container = app.state.container
    async with container.database.sessions() as session:
        actor = await container.identity.principal_for_user(
            session, UUID((await admin.get('/api/auth/me')).json()['id'])
        )
        check = await container.models.configuration.prepare(
            session, actor, {'capacity_note': 'original check'}, UUID(model['id'])
        )
        assert check.provider is None
    changed = await admin.patch('/api/models/' + model['id'], json={'name': 'concurrent name'})
    assert changed.status_code == 200, changed.text
    async with container.database.sessions.begin() as session:
        with pytest.raises(AppError) as conflict:
            await container.models.configuration.save_verified(session, actor, check)
        assert conflict.value.code == 'model_configuration_changed'
    current = next(
        row for row in (await admin.get('/api/models?include_disabled=true')).json() if row['id'] == model['id']
    )
    assert current['name'] == 'concurrent name' and current['capacity_note'] == ''


async def test_all_write_paths_validate_capacity_before_partial_changes(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await saved_model(admin, provider)
    container = app.state.container
    async with container.database.sessions.begin() as session:
        actor = await container.identity.principal_for_user(
            session, UUID((await admin.get('/api/auth/me')).json()['id'])
        )
        for fields in [
            {'name': 'must not persist', 'context_window_tokens': 1024, 'max_output_tokens': 1000},
            {'name': 'must not persist', 'enabled': 'true'},
            {'name': 'must not persist', 'model_type': 'embedding'},
        ]:
            with pytest.raises(AppError):
                await container.models.catalog.update(session, actor, UUID(model['id']), fields)
    async with container.database.sessions() as session:
        row = await session.get(ModelDefinition, UUID(model['id']))
        assert row is not None and row.name == model['name'] and row.context_window_tokens is None
    provider.catalog_requests.clear()
    bad = await admin.patch('/api/models/' + model['id'], json={'capacity_note': 'bad\x00note'})
    assert bad.status_code == 422 and not provider.catalog_requests


async def test_note_status_and_read_only_directory_do_not_claim_upstream_health(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await saved_model(admin, provider)
    response = await admin.patch('/api/models/' + model['id'], json={'context_window_tokens': 1000000})
    assert response.status_code == 200 and response.json()['capacity_status'] == 'unverified'
    provider.catalog_requests.clear()
    provider.catalog_status = 500
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(text('SET TRANSACTION READ ONLY'))
        rows = await app.state.container.models.catalog.list(session)
        row = next(row for row in rows if str(row.id) == model['id'])
        assert row.availability is not None and row.availability.available
    assert not provider.catalog_requests and not provider.requests
    cleared = await admin.patch(
        '/api/models/' + model['id'], json={'context_window_tokens': None, 'capacity_note': 'reference only'}
    )
    assert cleared.status_code == 200 and cleared.json()['capacity_status'] == 'unknown'


async def test_evidence_migration_preserves_old_rows_and_binding_serialization(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await saved_model(admin, provider)
    container = app.state.container
    async with container.database.sessions.begin() as session:
        before = (await container.models.bindings.resolve(session, UUID(model['id']))).model_dump(mode='json')
    path = Path(__file__).parents[3] / 'migrations/versions/0037_model_capacity_note.py'
    spec = spec_from_file_location('model_capacity_note_migration', path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    async with container.database.engine.begin() as connection:
        original = await connection.scalar(
            text("SELECT to_jsonb(m)-'capacity_note' FROM models m WHERE id=:id"), {'id': UUID(model['id'])}
        )
        await connection.execute(text('ALTER TABLE models DROP COLUMN capacity_note'))

        def upgrade(sync_connection: object) -> None:
            with Operations.context(MigrationContext.configure(sync_connection)):
                module.upgrade()

        await connection.run_sync(upgrade)
        after = await connection.scalar(
            text("SELECT to_jsonb(m)-'capacity_note' FROM models m WHERE id=:id"), {'id': UUID(model['id'])}
        )
        assert after == original
        assert (
            await connection.scalar(text('SELECT capacity_note FROM models WHERE id=:id'), {'id': UUID(model['id'])})
            == ''
        )
    async with container.database.sessions.begin() as session:
        after = (await container.models.bindings.resolve(session, UUID(model['id']))).model_dump(mode='json')
    assert after == before and 'capacity_note' not in after
