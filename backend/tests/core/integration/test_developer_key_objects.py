import hashlib
import json
import secrets
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import Connection

from tests.core.integration.test_agents import config
from zhiheng.bootstrap.migrations import migrate, migration_directory


async def test_issuance_requires_explicit_scopes_and_current_application_objects(
    admin: AsyncClient, model_id: str
) -> None:
    async def application(name: str) -> str:
        return (await admin.post('/api/developer/applications', json={'name': name})).json()['id']

    app_id, other_app = await application('Project'), await application('Other')
    agent = (await admin.post('/api/agents', json=config(model_id, application_id=app_id))).json()
    keys = f'/api/developer/applications/{app_id}/keys'
    candidates = f'/api/developer/applications/{app_id}/agent-candidates'
    assert (await admin.get(candidates)).json() == {'items': []}
    selected = {
        'name': 'Mixed',
        'scopes': ['models:invoke', 'agents:invoke'],
        'model_ids': [model_id],
        'agent_ids': [agent['id']],
    }
    assert (await admin.post(keys, json=selected)).status_code == 404
    assert (await admin.post(f'/api/agents/{agent["id"]}/enable', json={'expected_version': 1})).status_code == 200
    assert (await admin.get(candidates)).json() == {'items': [{'id': agent['id'], 'name': agent['name']}]}
    assert (await admin.post(f'/api/developer/applications/{other_app}/keys', json=selected)).status_code == 404
    assert (await admin.post(keys, json={'name': 'Implicit', 'model_ids': [model_id]})).status_code == 422
    assert (await admin.post(keys, json={**selected, 'authorization_version': 1})).status_code == 422
    assert (
        await admin.post(
            keys,
            json={
                'name': 'Unbound',
                'scopes': ['files:read'],
                'resource_grants': [{'kind': 'file', 'resource_id': str(uuid4()), 'actions': ['read']}],
            },
        )
    ).status_code == 422
    issued = await admin.post(keys, json=selected)
    assert issued.status_code == 201, issued.text
    rotated = await admin.post(f'{keys}/{issued.json()["id"]}/rotate', json={'grace_period_seconds': 0})
    assert rotated.status_code == 201, rotated.text
    for key in (
        'scopes',
        'model_ids',
        'agent_ids',
        'resource_grants',
        'create_resource_kinds',
    ):
        assert issued.json()[key] == rotated.json()[key]


async def test_upgrade_retires_old_credentials_and_keeps_audit_identity(
    admin: AsyncClient, app: FastAPI, model_id: str
) -> None:
    container = app.state.container
    app_id = (await admin.post('/api/developer/applications', json={'name': 'Existing'})).json()['id']
    alembic = Config()
    alembic.set_main_option('script_location', str(migration_directory()))

    def previous_schema(connection: Connection) -> None:
        alembic.attributes['connection'] = connection
        command.downgrade(alembic, '0043_workspace_execution')

    key_id = uuid4()
    secret = secrets.token_urlsafe(32)
    async with container.database.engine.begin() as connection:
        await connection.run_sync(previous_schema)
        await connection.execute(
            text("INSERT INTO developer_keys (id, application_id, name, prefix, digest, status, model_ids, currency, created_at) "
                 "VALUES (:id, :app, 'Existing', :prefix, :digest, 'active', CAST(:models AS jsonb), 'USD', now())"),
            {'id': key_id, 'app': UUID(app_id), 'prefix': secret[:18], 'digest': hashlib.sha256(secret.encode()).hexdigest(),
             'models': json.dumps([model_id])},
        )
    await migrate(container.settings)
    headers = {'Authorization': 'Bearer ' + secret}
    assert (await admin.get('/v1/models', headers=headers)).status_code == 401
    url = f'/api/developer/applications/{app_id}/keys'
    assert (await admin.get(url)).json()['total'] == 0
    assert (await admin.post(f'{url}/{key_id}/rotate', json={'grace_period_seconds': 0})).status_code == 404
    async with container.database.engine.connect() as connection:
        row = (await connection.execute(text(
            "SELECT application_id, status, deleted_at IS NOT NULL AS hidden, prefix, digest FROM developer_keys WHERE id=:id"
        ), {'id': key_id})).one()
        assert tuple(row) == (UUID(app_id), 'revoked', True, '', f'retired:{key_id}')
