"""Key capabilities remain independent of model allowlists and rotate exactly."""

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from zhiheng.shared.errors import AppError


async def application(admin: httpx.AsyncClient) -> dict:
    result = await admin.post('/api/developer/applications', json={'code': uuid4().hex, 'name': 'Resources'})
    assert result.status_code == 201, result.text
    return result.json()


async def issue(admin: httpx.AsyncClient, app_id: str, *, scopes: list[str], **body: Any) -> dict:
    """Sign a matrix fixture against today's concrete objects using the public Console contract."""
    path = f'/api/developer/applications/{app_id}'
    kinds = {'files': 'file', 'knowledge': 'knowledge_base', 'skills': 'skill'}
    actions = {
        (kind, action)
        for namespace, kind in kinds.items()
        for action in ('read', 'write', 'delete', 'query')
        if f'{namespace}:{"write" if action == "delete" else action}' in scopes
    }
    if actions and 'resource_grants' not in body:
        current = await admin.get(path + '/resources')
        assert current.status_code == 200, current.text
        selected = []
        for item in current.json():
            allowed = [action for action in item['actions'] if (item['kind'], action) in actions]
            if allowed:
                selected.append({'kind': item['kind'], 'resource_id': item['resource_id'], 'actions': allowed})
        body['resource_grants'] = selected
        body.setdefault('create_resource_kinds', [kind for kind in kinds.values() if (kind, 'write') in actions])
    result = await admin.post(path + '/keys', json={'name': 'Resource key', 'scopes': scopes, **body})
    assert result.status_code == 201, result.text
    return result.json()


async def file_reader(admin: httpx.AsyncClient, app_id: str, **limits: object) -> dict:
    """Seed one private file as test data so read-only credentials have a real selected object."""
    from zhiheng.api.common.dependencies import COOKIE_NAME

    container = admin._transport.app.state.container
    async with container.database.sessions.begin() as session:
        principal = await container.identity.authenticate(session, admin.cookies.get(COOKIE_NAME))
    file = await container.file_commands.upload(principal, filename='reader-fixture.txt', content=b'fixture')
    async with container.database.sessions.begin() as session:
        application_info = await container.applications.require(session, principal, UUID(app_id), lock=True)
        await container.resource_access.bind(
            session, principal, UUID(app_id), 'file', file.id, ['read'], expected_version=application_info.version
        )
    return await issue(
        admin,
        app_id,
        scopes=['files:read'],
        resource_grants=[
            {'kind': 'file', 'resource_id': str(file.id), 'actions': ['read']},
        ],
        **limits,
    )


async def test_resource_key_needs_no_models_and_cannot_invoke(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI
) -> None:
    owner_app = await application(admin)
    key = await file_reader(admin, owner_app['id'])
    assert key['model_ids'] == [] and key['resource_rpm_limit'] == 120
    invalid = await admin.post(
        f'/api/developer/applications/{owner_app["id"]}/keys',
        json={
            'name': 'Invalid',
            'scopes': ['files:read'],
            'resource_grants': key['resource_grants'],
            'model_ids': [model_id],
        },
    )
    assert invalid.status_code == 422
    headers = {'Authorization': f'Bearer {key["api_key"]}'}
    assert (await admin.get('/v1/models', headers=headers)).status_code == 403
    response = await admin.post(
        '/v1/chat/completions',
        headers=headers,
        json={'model': model_id, 'messages': [{'role': 'user', 'content': 'hello'}]},
    )
    assert response.status_code == 403, response.text


async def test_issuance_requires_explicit_scopes_and_rotation_copies_capabilities(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    owner_app = await application(admin)
    missing_scope = await admin.post(
        f'/api/developer/applications/{owner_app["id"]}/keys',
        json={
            'name': 'Invalid',
            'model_ids': [model_id],
        },
    )
    assert missing_scope.status_code == 422
    resource = await file_reader(admin, owner_app['id'], resource_rpm_limit=7)
    rotated = await admin.post(
        f'/api/developer/applications/{owner_app["id"]}/keys/{resource["id"]}/rotate', json={'grace_period_seconds': 0}
    )
    assert rotated.status_code == 201, rotated.text
    assert rotated.json()['scopes'] == resource['scopes']
    assert rotated.json()['resource_rpm_limit'] == 7
    assert rotated.json()['model_ids'] == []
    assert rotated.json()['resource_grants'] == resource['resource_grants']


@pytest.mark.parametrize(
    'body',
    [
        {},
        {'scopes': []},
        {'scopes': ['files:read', 'files:read']},
        {'scopes': ['files:root']},
        {'scopes': ['models:invoke']},
        {'scopes': ['files:read'], 'resource_rpm_limit': 121},
    ],
)
async def test_invalid_capabilities_rejected(admin: httpx.AsyncClient, body: dict[str, Any]) -> None:
    owner_app = await application(admin)
    result = await admin.post(f'/api/developer/applications/{owner_app["id"]}/keys', json={'name': 'Invalid', **body})
    assert result.status_code == 422, result.text


async def test_knowledge_scope_authorizes_embedding_without_exposing_inference(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI
) -> None:
    owner_app = await application(admin)
    key = await issue(
        admin, owner_app['id'], scopes=['knowledge:query', 'knowledge:write'], create_resource_kinds=['knowledge_base']
    )
    c = app.state.container
    async with c.database.sessions.begin() as session:
        identity = await c.keys.authenticate(session, key['api_key'], lock=True)
        await c.keys.authorize_identity(session, identity, [], scope='knowledge:query', lock=True)
        with pytest.raises(AppError) as error:
            await c.keys.authorize_identity(session, identity, [UUID(model_id)], scope='models:invoke', lock=True)
        assert error.value.status_code == 403


async def test_locked_identity_refreshes_cached_key_after_revocation(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI
) -> None:
    import asyncio

    from sqlalchemy import select

    from zhiheng.modules.developer.orm import DeveloperKey

    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['models:invoke'], model_ids=[model_id])
    c = app.state.container
    async with c.database.sessions() as waiting, c.database.sessions.begin() as changing:
        identity = await c.keys.authenticate(waiting, key['api_key'])
        cached = await waiting.get(DeveloperKey, UUID(key['id']))
        row = await changing.scalar(select(DeveloperKey).where(DeveloperKey.id == cached.id).with_for_update())
        row.status = 'revoked'
        check = asyncio.create_task(c.keys.authorize_identity(waiting, identity, [], scope='models:invoke', lock=True))
        await asyncio.sleep(0.05)
        assert not check.done()
        await changing.commit()
        with pytest.raises(AppError) as error:
            await check
        assert error.value.status_code == 401


@pytest.mark.parametrize('disable', ['application', 'owner'])
async def test_current_resource_caller_rejects_disabled_app_or_owner(
    admin: httpx.AsyncClient, app: FastAPI, disable: str
) -> None:
    owner_app = await application(admin)
    key = await file_reader(admin, owner_app['id'])
    c = app.state.container
    async with c.database.sessions.begin() as session:
        caller = await c.resource_access.bearer(session, key['api_key'], scope='files:read')
    if disable == 'application':
        assert (await admin.post(f'/api/developer/applications/{owner_app["id"]}/disable', json={})).status_code == 200
    else:
        from zhiheng.modules.identity.orm import IdentityUser

        async with c.database.sessions.begin() as session:
            row = await session.get(IdentityUser, caller.principal.id)
            row.is_active = False
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as error:
            await c.resource_access.current(session, caller, 'files:read')
        assert error.value.status_code == 401


async def test_model_key_has_no_resource_scope(admin: httpx.AsyncClient, model_id: str, app: FastAPI) -> None:
    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['models:invoke'], model_ids=[model_id])
    c = app.state.container
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as error:
            await c.resource_access.bearer(session, key['api_key'], scope='files:read')
        assert error.value.status_code == 403


async def test_resource_key_cannot_invoke_public_agent(admin: httpx.AsyncClient, model_id: str, app: FastAPI) -> None:
    from tests.core.integration.test_agents import config

    owner_app = await application(admin)
    key = await file_reader(admin, owner_app['id'])
    agent = await admin.post('/api/agents', json=config(model_id, application_id=owner_app['id']))
    assert agent.status_code == 201, agent.text
    agent_id = agent.json()['id']
    published = await admin.post(f'/api/agents/{agent_id}/enable', json={'expected_version': 1})
    assert published.status_code == 200, published.text
    result = await admin.post(
        f'/v1/agents/{agent_id}/chat/completions',
        headers={'Authorization': f'Bearer {key["api_key"]}'},
        json={'user': 'test-user', 'message': 'hello', 'client_request_id': str(uuid4())},
    )
    assert result.status_code == 403, result.text


async def test_key_preparation_rejects_a_foreign_owner_before_admission(admin: httpx.AsyncClient, app: FastAPI) -> None:
    from sqlalchemy import select

    from zhiheng.application.knowledge.preparation import PreparationStart
    from zhiheng.modules.identity.orm import IdentityUser

    owner_app = await application(admin)
    key = await issue(
        admin, owner_app['id'], scopes=['knowledge:query', 'knowledge:write'], create_resource_kinds=['knowledge_base']
    )
    c = app.state.container
    async with c.database.sessions.begin() as session:
        caller = await c.resource_access.bearer(session, key['api_key'], scope='knowledge:query')
        foreign = await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'member'))
        body = PreparationStart(
            owner_id=foreign,
            resource_type='knowledge_query',
            resource_id=uuid4(),
            request_id=uuid4(),
            value='f' * 64,
            request={'query': 'hello'},
            base_ids=[],
            dependencies={},
        )
        with pytest.raises(AppError) as error:
            await c.knowledge_preparation.start(
                session, principal=caller.principal, body=body, identity=caller.identity
            )
        assert error.value.status_code == 403


async def test_locked_identity_refreshes_cached_application_after_disable(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    import asyncio

    from sqlalchemy import select

    from zhiheng.modules.developer.orm import DeveloperApplication

    owner_app = await application(admin)
    key = await file_reader(admin, owner_app['id'])
    c = app.state.container
    async with c.database.sessions() as waiting, c.database.sessions.begin() as changing:
        identity = await c.keys.authenticate(waiting, key['api_key'])
        cached = await waiting.get(DeveloperApplication, UUID(owner_app['id']))
        row = await changing.scalar(
            select(DeveloperApplication).where(DeveloperApplication.id == cached.id).with_for_update()
        )
        row.status = 'disabled'
        check = asyncio.create_task(c.keys.authorize_identity(waiting, identity, [], scope='files:read', lock=True))
        await asyncio.sleep(0.05)
        assert not check.done()
        await changing.commit()
        with pytest.raises(AppError) as error:
            await check
        assert error.value.status_code == 401
