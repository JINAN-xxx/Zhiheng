"""Console discovery uses current delegation, never administrator resource visibility."""

from uuid import UUID

import httpx
from fastapi import FastAPI

from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.provider import ProviderServer


async def test_resource_catalog_and_console_only_summary(admin: httpx.AsyncClient, app: FastAPI) -> None:
    owner_app = await application(admin)
    catalog = (await admin.get('/api/developer/applications/scope-catalog')).json()
    assert {item['scope'] for item in catalog['definitions']} == set(catalog['scopes'])
    assert catalog['caps']['resource_rpm_max'] == 120
    assert {item['scope'] for item in catalog['definitions'] if item['requires_models']} == {
        'models:invoke',
        'agents:invoke',
    }
    endpoint = '/api/developer/applications/' + owner_app['id'] + '/resource-access'
    summary = await admin.get(endpoint)
    assert summary.status_code == 200, summary.text
    assert summary.json() == {
        'application_version': owner_app['version'],
        'source_count': 0,
        'source_bytes': 0,
        'source_file_limit': 1000,
        'source_byte_limit': 1073741824,
        'can_bind_resources': True,
        'can_configure_quota': False,
        'items': [],
    }
    key = await issue(admin, owner_app['id'], scopes=['files:read'])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        assert (await anonymous.get(endpoint, headers={'Authorization': 'Bearer ' + key['api_key']})).status_code == 401


async def test_owned_unindexed_and_disabled_candidates_and_shared_acl(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    owner_app = await application(admin)
    prefix = '/api/developer/applications/' + owner_app['id']
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Unindexed', 'embed_model_id': model})).json()
    candidates = await admin.get(prefix + '/resource-candidates?kind=knowledge_base')
    assert candidates.status_code == 200, candidates.text
    own = next(item for item in candidates.json() if item['resource_id'] == base['id'])
    assert own['display_name'] == 'Unindexed' and own['status'] == 'empty'
    assert set(own['allowed_actions']) == {'read', 'write', 'query', 'delete'}
    bound = await admin.put(
        prefix + '/resources/knowledge_base/' + base['id'],
        json={'actions': ['read', 'query'], 'expected_version': owner_app['version']},
    )
    assert bound.status_code == 200, bound.text
    stale = await admin.put(
        prefix + '/resources/knowledge_base/' + base['id'],
        json={'actions': ['read'], 'expected_version': owner_app['version']},
    )
    assert stale.status_code == 409
    summary = (await admin.get(prefix + '/resource-access')).json()
    assert summary['application_version'] == bound.json()['application_version']
    assert summary['items'][0]['display_name'] == 'Unindexed'
    assert summary['items'][0]['available'] is True
    c = app.state.container
    from sqlalchemy import select

    from zhiheng.modules.identity.orm import IdentityUser
    from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeGrant

    async with c.database.sessions.begin() as session:
        member = await session.scalar(select(IdentityUser).where(IdentityUser.username == 'member'))
        foreign = KnowledgeBase(owner_id=member.id, name='Private foreign', embed_model_id=UUID(model))
        session.add(foreign)
        await session.flush()
        foreign_id = str(foreign.id)
    assert foreign_id not in {
        row['resource_id'] for row in (await admin.get(prefix + '/resource-candidates?kind=knowledge_base')).json()
    }
    async with c.database.sessions.begin() as session:
        session.add(KnowledgeGrant(base_id=UUID(foreign_id), user_id=UUID(owner_app['owner_id']), effect='allow'))
    shared = next(
        row
        for row in (await admin.get(prefix + '/resource-candidates?kind=knowledge_base')).json()
        if row['resource_id'] == foreign_id
    )
    assert set(shared['allowed_actions']) == {'read', 'query'} and shared['owned'] is False
    bound = await admin.put(
        prefix + '/resources/knowledge_base/' + foreign_id,
        json={'actions': ['read'], 'expected_version': summary['application_version']},
    )
    assert bound.status_code == 200, bound.text
    async with c.database.sessions.begin() as session:
        grant = await session.get(KnowledgeGrant, (UUID(foreign_id), UUID(owner_app['owner_id'])))
        await session.delete(grant)
    opaque = next(
        row
        for row in (await admin.get(prefix + '/resource-access')).json()['items']
        if row['resource_id'] == foreign_id
    )
    assert opaque['display_name'] == '资源已失效' and opaque['available'] is False and opaque['allowed_actions'] == []
    assert 'Private foreign' not in str(opaque)
    removed = await admin.request(
        'DELETE',
        prefix + '/resources/knowledge_base/' + foreign_id,
        json={'expected_version': bound.json()['application_version']},
    )
    assert removed.status_code == 200, removed.text


async def test_file_usage_survives_unbind_and_admin_does_not_discover_foreign_sources(
    admin: httpx.AsyncClient,
    app: FastAPI,
) -> None:
    from tests.core.integration.test_resource_files import post

    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['files:read', 'files:write'])
    file = (await post(admin, key)).json()
    prefix = '/api/developer/applications/' + owner_app['id']
    before = (await admin.get(prefix + '/resource-access')).json()
    assert before['source_count'] == 1 and before['source_bytes'] == file['size']
    removed = await admin.request(
        'DELETE', prefix + '/resources/file/' + file['id'], json={'expected_version': before['application_version']}
    )
    assert removed.status_code == 200, removed.text
    after = (await admin.get(prefix + '/resource-access')).json()
    assert after['source_count'] == before['source_count'] and after['source_bytes'] == before['source_bytes']
    assert after['items'] == [] and after['application_version'] == removed.json()['version']
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        assert (
            await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        ).status_code == 200
        foreign_app = await application(member)
        foreign = await member.post(
            '/api/files?filename=private-member.txt',
            content=b'private',
            headers={'Content-Type': 'application/octet-stream'},
        )
        assert foreign.status_code == 201, foreign.text
        assert (await member.get(prefix + '/resource-access')).status_code == 404
    candidates = (await admin.get(prefix + '/resource-candidates?kind=file')).json()
    assert foreign.json()['id'] not in {row['resource_id'] for row in candidates}
    foreign_summary = await admin.get('/api/developer/applications/' + foreign_app['id'] + '/resource-access')
    assert foreign_summary.status_code == 404
    assert foreign.json()['id'] not in foreign_summary.text
    assert (
        await admin.get('/api/developer/applications/' + foreign_app['id'] + '/resource-candidates?kind=file')
    ).status_code == 404


async def test_owned_disabled_and_deleted_skill_can_be_explicitly_authorized_for_restore(
    admin: httpx.AsyncClient,
) -> None:
    owner_app = await application(admin)
    prefix = '/api/developer/applications/' + owner_app['id']
    prepared = await admin.post(
        '/api/skills/import/prepare?filename=SKILL.md',
        content=b'---\nname: candidate\ndescription: Personal restore\n---\nUse supplied facts.',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert prepared.status_code == 201, prepared.text
    installed = await admin.post('/api/skills/import/' + prepared.json()['draft_id'] + '/confirm', json={})
    assert installed.status_code == 201, installed.text
    skill = installed.json()
    disabled = await admin.patch(
        '/api/skills/' + skill['id'] + '/status', json={'status': 'disabled', 'expected_version': skill['version']}
    )
    assert disabled.status_code == 200, disabled.text
    candidate = next(
        row
        for row in (await admin.get(prefix + '/resource-candidates?kind=skill')).json()
        if row['resource_id'] == skill['id']
    )
    assert candidate['status'] == 'disabled' and set(candidate['allowed_actions']) == {'read', 'write', 'delete'}
    removed = await admin.request('DELETE', '/api/skills/' + skill['id'], json={})
    assert removed.status_code == 200, removed.text
    candidates = (await admin.get(prefix + '/resource-candidates?kind=skill')).json()
    candidate = next(row for row in candidates if row['resource_id'] == skill['id'])
    assert candidate['status'] == 'deleted'
    granted = await admin.put(
        prefix + '/resources/skill/' + skill['id'],
        json={'actions': ['read', 'write'], 'expected_version': owner_app['version']},
    )
    assert granted.status_code == 200, granted.text
