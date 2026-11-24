"""Object-scoped resource Keys use current App/ACL bounds and their own creation family."""

from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.auth_resource_support import without_grants
from tests.core.integration.test_resource_files import auth, envelope, post
from tests.core.integration.test_resource_key_scopes import application
from tests.core.integration.test_resource_knowledge import finish, setup
from tests.core.integration.test_resource_skills import preview
from tests.core.provider import ProviderServer
from tests.core.unit.test_skill_package import markdown
from zhiheng.modules.billing.orm import Invocation


async def issue(admin: httpx.AsyncClient, application_id: str, **policy: object) -> dict:
    response = await admin.post(
        f'/api/developer/applications/{application_id}/keys', json={'name': 'Object-scoped Key', **policy}
    )
    assert response.status_code == 201, response.text
    return response.json()


def grant(kind: str, resource_id: str, *actions: str) -> dict:
    return {'kind': kind, 'resource_id': resource_id, 'actions': list(actions)}


async def test_file_creation_family_filtered_pages_replay_rotation_and_app_revocation(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    app_id = (await application(admin))['id']
    policy = {'scopes': ['files:read', 'files:write'], 'create_resource_kinds': ['file']}
    owner = await issue(admin, app_id, **policy)
    unrelated = await issue(admin, app_id, **policy)
    external_id = uuid4()
    first = await post(admin, owner, external_id)
    assert first.status_code == 201, first.text
    other_file = await post(admin, unrelated)
    second = await post(admin, owner)
    assert other_file.status_code == second.status_code == 201
    path = '/api/v1/files/' + first.json()['id']
    envelope(await admin.get(path, headers=auth(unrelated)), 404)
    envelope(await post(admin, unrelated, external_id), 404)
    reader = await issue(
        admin, app_id, scopes=['files:read'], resource_grants=[grant('file', first.json()['id'], 'read')]
    )
    assert (await admin.get(path + '/content', headers=auth(reader))).content == b'four'
    envelope(await admin.get('/api/v1/files/' + second.json()['id'] + '/content', headers=auth(reader)), 404)

    rotated = await admin.post(
        f'/api/developer/applications/{app_id}/keys/{owner["id"]}/rotate', json={'grace_period_seconds': 0}
    )
    assert rotated.status_code == 201, rotated.text
    headers = auth(rotated.json())
    page = (await admin.get('/api/v1/files?limit=1', headers=headers)).json()
    assert [row['id'] for row in page['items']] == [second.json()['id']] and page['has_more']
    page = (
        await admin.get('/api/v1/files', headers=headers, params={'cursor': page['next_cursor'], 'limit': 1})
    ).json()
    assert [row['id'] for row in page['items']] == [first.json()['id']] and not page['has_more']
    async with without_grants(app, app_id):
        envelope(await admin.get(path + '/content', headers=headers), 404)
        assert (await admin.get('/api/v1/files', headers=headers)).json()['items'] == []
    envelope(await admin.get(path, headers=auth(owner)), 401)


async def test_mixed_key_queries_one_base_indexes_another_and_charges_internal_models(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    application_info, seed, a = await setup(admin, provider, app)
    b = await admin.post(
        '/api/v1/knowledge-bases',
        headers=auth(seed),
        json={'name': 'Other library', 'embed_model_id': a['embed_model_id']},
    )
    assert b.status_code == 201, b.text
    source = (await post(admin, seed)).json()['id']
    app_id = application_info['id']
    path_a = '/api/v1/knowledge-bases/' + a['id']
    path_b = '/api/v1/knowledge-bases/' + b.json()['id']
    admission = await admin.post(path_a + '/documents', headers=auth(seed), json={'file_ids': [source]})
    assert admission.status_code == 202, admission.text
    assert (await finish(admin, app, seed, admission.json()))['status'] == 'succeeded'
    mixed = await issue(
        admin,
        app_id,
        scopes=['files:read', 'knowledge:read', 'knowledge:query', 'knowledge:write'],
        resource_grants=[
            grant('knowledge_base', a['id'], 'query'),
            grant('knowledge_base', b.json()['id'], 'read', 'write'),
            grant('file', source, 'read'),
        ],
    )
    headers = auth(mixed)
    assert mixed['model_ids'] == []
    envelope(await admin.post(path_a + '/folders', headers=headers, json={'name': 'denied'}), 404)
    envelope(await admin.post(path_b + '/query', headers=headers, json={'query': 'four'}), 404)
    assert [row['id'] for row in (await admin.get('/api/v1/knowledge-bases', headers=headers)).json()['items']] == [
        b.json()['id']
    ]
    envelope(await admin.get(path_a + '/documents', headers=headers), 404)
    queued = await admin.post(path_b + '/documents', headers=headers, json={'file_ids': [source]})
    assert queued.status_code == 202, queued.text
    assert (await finish(admin, app, mixed, queued.json()))['status'] == 'succeeded'

    query = await admin.post(path_a + '/query', headers=headers, json={'query': 'four'})
    assert query.status_code == 202, query.text
    envelope(await admin.post(query.headers['location'] + '/cancel', headers=headers), 404)
    manager = await issue(
        admin,
        app_id,
        scopes=['knowledge:query', 'knowledge:write'],
        resource_grants=[grant('knowledge_base', a['id'], 'query', 'write')],
    )
    assert (await admin.get(query.headers['location'], headers=auth(manager))).json()['cancel_allowed']
    app_path = f'/api/developer/applications/{app_id}'
    app_version = (await admin.get(app_path)).json()['version']
    narrowed = await admin.put(
        f'{app_path}/resources/knowledge_base/{a["id"]}',
        json={'actions': ['read', 'query'], 'expected_version': app_version},
    )
    assert narrowed.status_code == 200, narrowed.text
    envelope(await admin.post(query.headers['location'] + '/cancel', headers=auth(manager)), 404)
    result = await finish(admin, app, mixed, query.json())
    assert result['status'] == 'succeeded' and result['result']['hits'][0]['content'] == 'four'
    assert (await admin.get('/v1/models', headers=headers)).status_code == 403
    async with app.state.container.database.sessions() as session:
        operations = await session.scalars(select(Invocation.operation).where(Invocation.key_id == UUID(mixed['id'])))
        assert set(operations) == {'embedding', 'rerank'}
    async with without_grants(app, app_id):
        envelope(await admin.get(query.headers['location'], headers=headers), 404)


async def test_skill_import_and_package_access_respect_key_object_selection(admin: httpx.AsyncClient) -> None:
    app_id = (await application(admin))['id']
    scopes = ['files:read', 'files:write', 'skills:read', 'skills:write']
    key = await issue(admin, app_id, scopes=scopes, create_resource_kinds=['file', 'skill'])
    draft = await preview(admin, key)
    assert draft.status_code == 201, draft.text
    installed = await admin.post(f'/api/v1/skill-imports/{draft.json()["draft_id"]}/confirm', headers=auth(key))
    assert installed.status_code == 201, installed.text
    skill = installed.json()
    second = await issue(admin, app_id, scopes=scopes, create_resource_kinds=['file', 'skill'])
    assert (await admin.get('/api/v1/skills', headers=auth(second))).json()['items'] == []
    envelope(await preview(admin, second), 404)
    package = f'/api/v1/skills/{skill["id"]}/versions/{skill["current_revision_id"]}/content'
    envelope(await admin.get(package, headers=auth(second)), 404)
    reader = await issue(admin, app_id, scopes=['skills:read'], resource_grants=[grant('skill', skill['id'], 'read')])
    assert (await admin.get(package, headers=auth(reader))).content == markdown()
