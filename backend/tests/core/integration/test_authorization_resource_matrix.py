"""Real target authorization matrix; all provider traffic uses the local test fixture."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.auth_resource_support import (
    RESOURCE_SCOPES,
    ResourceWorld,
    domain_facts,
    other_key,
    request_headers,
    resource_world,
    without_grants,
)
from tests.core.integration.test_agents import config
from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_permission_tree import allow
from tests.core.integration.test_public_protocol import drive
from tests.core.integration.test_resource_files import auth
from tests.core.integration.test_resource_files import post as upload_resource
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.integration.test_resource_knowledge import finish
from tests.core.integration.test_resource_skills import preview
from tests.core.integration.test_work import member_client as unconfigured_member_client
from tests.core.provider import ProviderServer
from tests.core.unit.test_skill_package import markdown
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.runtime.orm import Run
from zhiheng.shared.permission_policy import OPERATIONS
from zhiheng.shared.time import utcnow
from zhiheng.worker.execution import execute_run


@asynccontextmanager
async def member_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # These cases test target ownership, Scope and application grants. Explicitly
    # allow domain operations so a missing role cannot mask the intended ACL check.
    for module in ('developer', 'knowledge', 'skills', 'agents', 'chat', 'work'):
        await allow(app, module, *OPERATIONS['platform:' + module])
    async with unconfigured_member_client(app) as client:
        yield client


@dataclass(frozen=True)
class ResourceOperation:
    name: str
    method: str
    path: str
    scope: str
    tag: str | None = None
    body: dict[str, Any] | None = None


WRITE_OPERATIONS = [
    ResourceOperation('file_delete', 'DELETE', '/api/v1/files/{file_id}', 'files:write', 'file'),
    ResourceOperation('file_restore', 'POST', '/api/v1/files/{trash_id}/restore', 'files:write', 'trash'),
    ResourceOperation(
        'base_patch', 'PATCH', '/api/v1/knowledge-bases/{base_id}', 'knowledge:write', 'base', {'name': 'forged'}
    ),
    ResourceOperation('base_delete', 'DELETE', '/api/v1/knowledge-bases/{base_id}', 'knowledge:write', 'base'),
    ResourceOperation(
        'folder_create', 'POST', '/api/v1/knowledge-bases/{base_id}/folders', 'knowledge:write', body={'name': 'forged'}
    ),
    ResourceOperation(
        'folder_patch',
        'PATCH',
        '/api/v1/knowledge-bases/{base_id}/folders/{folder_id}',
        'knowledge:write',
        'folder',
        {'name': 'forged'},
    ),
    ResourceOperation(
        'folder_delete', 'DELETE', '/api/v1/knowledge-bases/{base_id}/folders/{folder_id}', 'knowledge:write', 'folder'
    ),
    ResourceOperation('document_import', 'POST', '/api/v1/knowledge-bases/{base_id}/documents', 'knowledge:write'),
    ResourceOperation(
        'knowledge_query',
        'POST',
        '/api/v1/knowledge-bases/{base_id}/query',
        'knowledge:query',
        body={'query': 'private'},
    ),
    ResourceOperation('base_rebuild', 'POST', '/api/v1/knowledge-bases/{base_id}/rebuild', 'knowledge:write', 'base'),
    ResourceOperation(
        'document_rechunk',
        'POST',
        '/api/v1/knowledge-bases/{base_id}/documents/{document_id}/rechunk',
        'knowledge:write',
        'document',
        {'chunk_size': 200, 'chunk_overlap': 20},
    ),
    ResourceOperation(
        'document_delete',
        'DELETE',
        '/api/v1/knowledge-bases/{base_id}/documents/{document_id}',
        'knowledge:write',
        'document',
    ),
    ResourceOperation('operation_cancel', 'POST', '/api/v1/operations/{operation_id}/cancel', 'knowledge:write'),
    ResourceOperation('skill_prepare', 'POST', '/api/v1/skill-imports', 'skills:write'),
    ResourceOperation('skill_confirm', 'POST', '/api/v1/skill-imports/{draft_id}/confirm', 'skills:write'),
    ResourceOperation(
        'skill_patch', 'PATCH', '/api/v1/skills/{skill_id}', 'skills:write', 'skill', {'status': 'disabled'}
    ),
    ResourceOperation('skill_delete', 'DELETE', '/api/v1/skills/{skill_id}', 'skills:write', 'skill'),
]

READ_OPERATIONS = [
    ResourceOperation('files_page', 'GET', '/api/v1/files', 'files:read'),
    ResourceOperation('file_get', 'GET', '/api/v1/files/{file_id}', 'files:read'),
    ResourceOperation('file_content', 'GET', '/api/v1/files/{file_id}/content', 'files:read'),
    ResourceOperation('bases_page', 'GET', '/api/v1/knowledge-bases', 'knowledge:read'),
    ResourceOperation('base_get', 'GET', '/api/v1/knowledge-bases/{base_id}', 'knowledge:read'),
    ResourceOperation('folders_page', 'GET', '/api/v1/knowledge-bases/{base_id}/folders', 'knowledge:read'),
    ResourceOperation('documents_page', 'GET', '/api/v1/knowledge-bases/{base_id}/documents', 'knowledge:read'),
    ResourceOperation('operations_page', 'GET', '/api/v1/operations', 'knowledge:read'),
    ResourceOperation('operation_get', 'GET', '/api/v1/operations/{operation_id}', 'knowledge:read'),
    ResourceOperation('skills_page', 'GET', '/api/v1/skills', 'skills:read'),
    ResourceOperation('skill_get', 'GET', '/api/v1/skills/{skill_id}', 'skills:read'),
    ResourceOperation('skill_versions', 'GET', '/api/v1/skills/{skill_id}/versions', 'skills:read'),
    ResourceOperation('skill_revision', 'GET', '/api/v1/skills/{skill_id}/versions/{revision_id}', 'skills:read'),
    ResourceOperation(
        'skill_content', 'GET', '/api/v1/skills/{skill_id}/versions/{revision_id}/content', 'skills:read'
    ),
]


def body_for(operation: ResourceOperation, world: ResourceWorld) -> dict[str, Any] | None:
    if operation.name == 'document_import':
        return {'file_ids': [world.file['id']], 'folder_id': world.folder['id']}
    if operation.name == 'skill_prepare':
        # Reuse the real file which produced the pending draft.
        return {'file_id': world.draft['_source_file_id']}
    return operation.body


def etag_for(operation: ResourceOperation, world: ResourceWorld, stale: bool) -> str | None:
    if operation.tag is None:
        return None
    kind = {
        'base': 'knowledge-base',
        'folder': 'knowledge-folder',
        'document': 'knowledge-document',
        'trash': 'file',
    }.get(operation.tag, operation.tag)
    target = getattr(world, operation.tag)
    version = target.get('resource_version', target.get('version'))
    return f'"{kind}-{999 if stale else version}"'


async def make_world(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    *,
    existing_model_id: str | None = None,
    legacy: bool = False,
) -> ResourceWorld:
    world = await resource_world(admin, app, provider, existing_model_id=existing_model_id, legacy=legacy)
    async with app.state.container.database.sessions() as session:
        from zhiheng.modules.developer.orm import APIRequest

        request = await session.scalar(select(APIRequest).where(APIRequest.result_id == UUID(world.draft['draft_id'])))
        assert request is not None
        world.draft['_source_file_id'] = next(target['id'] for target in request.resources if target['kind'] == 'file')
    return world


@pytest.mark.parametrize('operation', WRITE_OPERATIONS, ids=lambda item: item.name)
@pytest.mark.parametrize('stale', [False, True], ids=['valid_etag', 'stale_etag'])
async def test_resource_write_authority_precedes_version(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, operation: ResourceOperation, stale: bool
) -> None:
    world = await make_world(admin, app, provider)
    endpoint = operation.path.format(**world.values())
    missing_scope = await issue(
        admin,
        world.application['id'],
        scopes=[s for s in RESOURCE_SCOPES if s != operation.scope],
        model_ids=[world.base['embed_model_id']],
    )
    sibling = await other_key(admin, [world.base['embed_model_id']])
    async with member_client(app) as member:
        foreign = await other_key(member, [world.base['embed_model_id']])
        before = await domain_facts(app)
        calls = len(provider.requests)
        for case, key, expected in [
            ('no-scope', missing_scope, 403),
            ('other-app', sibling, 404),
            ('foreign-owner', foreign, 404),
        ]:
            response = await admin.request(
                operation.method,
                endpoint,
                headers={**request_headers(key, etag=etag_for(operation, world, stale)), 'X-Auth-Audit-Case': case},
                json=body_for(operation, world),
            )
            assert response.status_code == expected, (operation.name, expected, response.text)
            assert 'ETag' not in response.headers
            assert await domain_facts(app) == before
            assert len(provider.requests) == calls
        async with without_grants(app, world.application['id']):
            response = await admin.request(
                operation.method,
                endpoint,
                headers={
                    **request_headers(world.key, etag=etag_for(operation, world, stale)),
                    'X-Auth-Audit-Case': 'no-app-grant',
                },
                json=body_for(operation, world),
            )
            assert response.status_code == 404, (operation.name, response.text)
            assert await domain_facts(app) == before
            assert len(provider.requests) == calls


@pytest.mark.parametrize('operation', READ_OPERATIONS, ids=lambda item: item.name)
async def test_resource_read_authorization_uses_real_target(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, operation: ResourceOperation
) -> None:
    world = await make_world(admin, app, provider)
    endpoint = operation.path.format(**world.values())
    allowed = await admin.get(endpoint, headers={**auth(world.key), 'X-Auth-Audit-Case': 'authorized-owner'})
    assert allowed.status_code == 200, allowed.text
    if operation.name.endswith('_page') or operation.name == 'skill_versions':
        assert allowed.json()['items'], (operation.name, allowed.text)
    elif operation.name == 'file_content':
        assert allowed.content == b'four'
    elif operation.name == 'skill_content':
        assert allowed.content.startswith(b'---\nname: ')
    else:
        assert allowed.json()['id'] in world.values().values()
    scopes = [s for s in RESOURCE_SCOPES if s != operation.scope]
    if operation.name == 'operations_page':
        scopes.remove('knowledge:query')
    insufficient = await issue(admin, world.application['id'], scopes=scopes)
    sibling = await other_key(admin, [])
    collections = {'files_page', 'bases_page', 'operations_page', 'skills_page'}
    async with member_client(app) as member:
        foreign = await other_key(member, [])
        before = await domain_facts(app)
        for case, key, expected in [
            ('no-scope', insufficient, 403),
            ('other-app', sibling, 200 if operation.name in collections else 404),
            ('foreign-owner', foreign, 200 if operation.name in collections else 404),
        ]:
            response = await admin.get(endpoint, headers={**auth(key), 'X-Auth-Audit-Case': case})
            assert response.status_code == expected, (operation.name, expected, response.text)
            if expected == 200:
                assert response.json()['items'] == []
            else:
                assert 'ETag' not in response.headers
        async with without_grants(app, world.application['id']):
            response = await admin.get(endpoint, headers={**auth(world.key), 'X-Auth-Audit-Case': 'no-app-grant'})
            assert response.status_code == (200 if operation.name in collections else 404), response.text
            if response.status_code == 200:
                assert response.json()['items'] == []
        assert await domain_facts(app) == before


@pytest.mark.parametrize('resource', ['files', 'knowledge-bases'])
async def test_resource_creation_requires_write_scope_and_owns_only_current_app(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, resource: str
) -> None:
    embed = await embedding_model(admin, provider)
    owner_app = await application(admin)
    scope = 'files:write' if resource == 'files' else 'knowledge:write'
    reader = await issue(admin, owner_app['id'], scopes=['files:read', 'knowledge:read'])
    before = await domain_facts(app)
    kwargs = (
        {'files': {'file': ('matrix.txt', b'private')}, 'data': {'purpose': 'knowledge'}}
        if resource == 'files'
        else {'json': {'name': 'Matrix', 'embed_model_id': embed}}
    )
    rejected = await admin.post('/api/v1/' + resource, headers=auth(reader), **kwargs)
    assert rejected.status_code == 403, rejected.text
    assert await domain_facts(app) == before and provider.requests == []
    writer = await issue(admin, owner_app['id'], scopes=[scope, 'files:read', 'knowledge:read'], model_ids=[embed])
    created = await admin.post('/api/v1/' + resource, headers=auth(writer), **kwargs)
    assert created.status_code == 201, created.text
    assert created.json()['owner_id'] == owner_app['owner_id']
    sibling = await other_key(admin, [])
    assert (
        await admin.get('/api/v1/' + resource + '/' + created.json()['id'], headers=auth(sibling))
    ).status_code == 404


@pytest.mark.parametrize(
    'child',
    [
        'folder_patch',
        'folder_delete',
        'folder_parent',
        'import_folder',
        'document_rechunk',
        'document_delete',
        'skill_revision',
        'skill_content',
    ],
)
@pytest.mark.parametrize('owner', ['same_owner', 'foreign_owner'])
async def test_nested_ids_must_belong_to_authorized_parent(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, child: str, owner: str
) -> None:
    world = await make_world(admin, app, provider)
    async with member_client(app) as member:
        client = admin if owner == 'same_owner' else member
        other = await make_world(client, app, provider, existing_model_id=world.base['embed_model_id'])
        if child in {'skill_revision', 'skill_content'}:
            old_revision = other.skill['current_revision_id']
            updated_draft = await preview(client, other.key, markdown(other.skill['slug'], 'Updated private revision'))
            assert updated_draft.status_code == 201, updated_draft.text
            updated = await client.post(
                f'/api/v1/skill-imports/{updated_draft.json()["draft_id"]}/confirm', headers=auth(other.key)
            )
            assert updated.status_code == 201 and updated.json()['current_revision_id'] != old_revision, updated.text
            # Attack with the real historical package, rather than only the latest revision.
            assert (
                await client.get(
                    f'/api/v1/skills/{other.skill["id"]}/versions/{old_revision}/content', headers=auth(other.key)
                )
            ).status_code == 200
        # Other App targets really exist and are readable by their proper Key.
        assert (
            await client.get(f'/api/v1/knowledge-bases/{other.base["id"]}', headers=auth(other.key))
        ).status_code == 200
        prefix = f'/api/v1/knowledge-bases/{world.base["id"]}'
        headers = request_headers(world.key)
        body = None
        method = 'POST'
        if child in {'folder_patch', 'folder_delete'}:
            method = 'PATCH' if child == 'folder_patch' else 'DELETE'
            endpoint = prefix + '/folders/' + other.folder['id']
            headers['If-Match'] = f'"knowledge-folder-{other.folder["version"]}"'
            body = {'name': 'stolen'} if method == 'PATCH' else None
        elif child in {'folder_parent', 'import_folder'}:
            endpoint = prefix + ('/folders' if child == 'folder_parent' else '/documents')
            body = (
                {'name': 'stolen', 'parent_id': other.folder['id']}
                if child == 'folder_parent'
                else {'file_ids': [world.file['id']], 'folder_id': other.folder['id']}
            )
        elif child in {'document_rechunk', 'document_delete'}:
            method = 'POST' if child == 'document_rechunk' else 'DELETE'
            endpoint = prefix + '/documents/' + other.document['id'] + ('/rechunk' if method == 'POST' else '')
            headers['If-Match'] = f'"knowledge-document-{other.document["version"]}"'
            body = {'chunk_size': 200, 'chunk_overlap': 20} if method == 'POST' else None
        else:
            method = 'GET'
            endpoint = f'/api/v1/skills/{world.skill["id"]}/versions/{other.skill["current_revision_id"]}'
            if child == 'skill_content':
                endpoint += '/content'
        before, calls = await domain_facts(app), len(provider.requests)
        result = await admin.request(
            method, endpoint, headers={**headers, 'X-Auth-Audit-Case': f'parent-mismatch-{owner}-{child}'}, json=body
        )
        assert result.status_code == 404, result.text
        assert 'ETag' not in result.headers
        after = await domain_facts(app)
        if child == 'import_folder':
            # Failed durable admission can retain its request identity, never business work.
            assert after[:9] == before[:9] and after[10:] == before[10:]
            added = set(after[9]) - set(before[9])
            assert len(added) == 1 and all(kind is None and target is None for _, kind, target in added)
        else:
            assert after == before
        assert len(provider.requests) == calls


INFERENCE_OPERATIONS = ['models', 'chat', 'embeddings', 'rerank', 'agent_chat', 'agent_preparation', 'agent_cancel']


async def inference_context(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str, operation: str
) -> tuple[dict[str, Any], dict[str, Any], str, str, dict[str, Any] | None, str]:
    """The target model, publication and preparation all exist before attack requests."""
    world = await make_world(admin, app, provider)
    connection = next(row['connection_id'] for row in (await admin.get('/api/models')).json() if row['id'] == model_id)
    rerank = await admin.post(
        '/api/models',
        json={'name': 'Matrix rerank', 'connection_id': connection, 'upstream_model': 'rerank', 'model_type': 'rerank'},
    )
    assert rerank.status_code == 201, rerank.text
    rerank_id = rerank.json()['id']
    for identifier, price in [
        (model_id, {'billing_mode': 'token', 'input_price': '0.01', 'output_price': '0.01'}),
        (rerank_id, {'billing_mode': 'search_unit', 'search_price': '0.01'}),
    ]:
        rated = await admin.post(f'/api/models/{identifier}/rate-cards', json=price)
        assert rated.status_code == 201, rated.text
    key = await issue(
        admin,
        world.application['id'],
        scopes=['models:invoke', 'agents:invoke'],
        model_ids=list(dict.fromkeys([model_id, rerank_id, *world.key['model_ids']])),
    )
    agent = await admin.post(
        '/api/agents',
        json=config(
            model_id,
            application_id=world.application['id'],
            knowledge_base_ids=[world.base['id']] if operation in {'agent_preparation', 'agent_cancel'} else [],
        ),
    )
    assert agent.status_code == 201, agent.text
    published = await admin.post(f'/api/agents/{agent.json()["id"]}/publish', json={'expected_version': 1})
    assert published.status_code == 200, published.text
    target = model_id
    method, endpoint, body = 'GET', '/v1/models', None
    if operation in {'chat', 'embeddings', 'rerank'}:
        endpoint = {'chat': '/v1/chat/completions', 'embeddings': '/v1/embeddings', 'rerank': '/v1/rerank'}[operation]
        target = {'chat': model_id, 'embeddings': world.base['embed_model_id'], 'rerank': rerank_id}[operation]
        body = {
            'model': target,
            **{
                'chat': {'messages': [{'role': 'user', 'content': 'matrix'}]},
                'embeddings': {'input': ['matrix'], 'dimensions': 2},
                'rerank': {'query': 'matrix', 'documents': ['private'], 'top_n': 1},
            }[operation],
        }
        method = 'POST'
    elif operation.startswith('agent_'):
        endpoint = f'/v1/agents/{agent.json()["id"]}/chat/completions'
        body = {'messages': [{'role': 'user', 'content': 'matrix'}]}
        method = 'POST'
        if operation != 'agent_chat':
            prepared = await admin.post(endpoint, headers=auth(key), json={**body, 'client_request_id': str(uuid4())})
            assert prepared.status_code == 202, prepared.text
            endpoint = prepared.json()['polling_url']
            method, body = 'GET', None
            if operation == 'agent_cancel':
                endpoint += '/cancel'
                method, body = 'POST', {}
    return world.application, key, method, endpoint, body, target


@pytest.mark.parametrize('operation', INFERENCE_OPERATIONS)
async def test_all_inference_operations_enforce_scope_identity_and_models(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str, operation: str
) -> None:
    owner_app, key, method, endpoint, body, target = await inference_context(admin, app, provider, model_id, operation)
    required = 'agents:invoke' if operation.startswith('agent_') else 'models:invoke'
    insufficient = await issue(admin, owner_app['id'], scopes=['files:read'], model_ids=key['model_ids'])
    before, calls = await domain_facts(app), len(provider.requests)
    for headers, expected in [
        ({'X-Auth-Audit-Case': 'cookie-only'}, 401),
        ({'Authorization': 'Bearer invalid', 'X-Auth-Audit-Case': 'invalid-bearer'}, 401),
        ({**auth(insufficient), 'X-Auth-Audit-Case': 'no-scope'}, 403),
    ]:
        response = await admin.request(method, endpoint, headers=headers, json=body)
        assert response.status_code == expected, (operation, required, response.text)
        assert 'run' not in response.json() and 'hits' not in response.json()
        assert await domain_facts(app) == before and len(provider.requests) == calls
    for state in (
        'revoked',
        'expired',
        'retirement_expired',
        'application_disabled',
        'application_deleted',
        'owner_disabled',
        'model_unlisted',
    ):
        async with app.state.container.database.sessions.begin() as session:
            current = await session.get(DeveloperKey, UUID(key['id']))
            application_row = await session.get(DeveloperApplication, UUID(owner_app['id']))
            owner = await session.get(IdentityUser, UUID(owner_app['owner_id']))
            assert current is not None and application_row is not None and owner is not None
            if state == 'revoked':
                current.status = 'revoked'
            elif state == 'expired':
                current.expires_at = utcnow() - timedelta(seconds=1)
            elif state == 'retirement_expired':
                current.status, current.retire_at = 'retiring', utcnow() - timedelta(seconds=1)
            elif state == 'application_disabled':
                application_row.status = 'disabled'
            elif state == 'application_deleted':
                application_row.deleted_at = utcnow()
            elif state == 'owner_disabled':
                owner.is_active = False
            else:
                # Simulate loss of the original allowlist, as existing replay tests do.
                current.model_ids = [value for value in current.model_ids if value != target]
        response = await admin.request(method, endpoint, headers={**auth(key), 'X-Auth-Audit-Case': state}, json=body)
        if state == 'model_unlisted' and operation == 'models':
            assert response.status_code == 200, response.text
            assert target not in {item['id'] for item in response.json()['data']}
            assert all(set(item) == {'id', 'object', 'created', 'owned_by'} for item in response.json()['data'])
        else:
            assert response.status_code == (404 if state == 'model_unlisted' else 401), (
                operation,
                state,
                response.text,
            )
            assert 'run' not in response.json() and 'hits' not in response.json()
        assert await domain_facts(app) == before and len(provider.requests) == calls
        async with app.state.container.database.sessions.begin() as session:
            current = await session.get(DeveloperKey, UUID(key['id']))
            application_row = await session.get(DeveloperApplication, UUID(owner_app['id']))
            owner = await session.get(IdentityUser, UUID(owner_app['owner_id']))
            current.status, current.expires_at, current.retire_at = 'active', None, None
            current.model_ids = key['model_ids']
            application_row.status, application_row.deleted_at, owner.is_active = 'active', None, True


@pytest.mark.parametrize('operation', ['chat', 'embeddings', 'rerank', 'agent_chat'])
async def test_original_inference_key_is_rechecked_before_local_provider(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str, operation: str
) -> None:
    _owner_app, key, method, endpoint, body, _target = await inference_context(
        admin, app, provider, model_id, operation
    )
    task = asyncio.create_task(admin.request(method, endpoint, headers=auth(key), json=body))
    try:
        pending = None
        for _ in range(100):
            async with app.state.container.database.sessions() as session:
                pending = await session.scalar(select(Run.id).where(Run.status == 'pending'))
            if pending is not None:
                break
            if task.done():
                raise AssertionError((await task).text)
            await asyncio.sleep(0.01)
        assert pending is not None
        async with app.state.container.database.sessions.begin() as session:
            original = await session.get(DeveloperKey, UUID(key['id']))
            original.status = 'revoked'
        calls = len(provider.requests)
        await execute_run(app.state.container, pending)
        result = await asyncio.wait_for(task, 5)
        assert result.status_code in {401, 502}, result.text
        assert len(provider.requests) == calls
        async with app.state.container.database.sessions() as session:
            run = await session.get(Run, pending)
            assert run.status in {'cancelled', 'failed'}, run.status
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize('kind', ['job', 'query'])
async def test_resource_operation_observer_does_not_replace_original_execution_key(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, kind: str
) -> None:
    world = await make_world(admin, app, provider)
    if kind == 'job':
        fresh = await upload_resource(admin, world.key)
        assert fresh.status_code == 201, fresh.text
        accepted = await admin.post(
            f'/api/v1/knowledge-bases/{world.base["id"]}/documents',
            headers=auth(world.key),
            json={'file_ids': [fresh.json()['id']]},
        )
    else:
        accepted = await admin.post(
            f'/api/v1/knowledge-bases/{world.base["id"]}/query', headers=auth(world.key), json={'query': 'four'}
        )
    assert accepted.status_code == 202, accepted.text
    observer = await issue(
        admin,
        world.application['id'],
        scopes=['knowledge:read', 'knowledge:query', 'knowledge:write'],
        model_ids=world.key['model_ids'] if kind == 'query' else [],
    )
    assert observer['model_ids'] == (world.key['model_ids'] if kind == 'query' else [])
    async with app.state.container.database.sessions.begin() as session:
        original = await session.get(DeveloperKey, UUID(world.key['id']))
        original.status = 'revoked'
    endpoint = '/api/v1/operations/' + accepted.json()['id']
    before, calls = await domain_facts(app), len(provider.requests)
    denied = await admin.get(endpoint, headers=auth(world.key))
    assert denied.status_code == 401, denied.text
    observed = await admin.get(endpoint, headers=auth(observer))
    assert observed.status_code == 200 and observed.json()['id'] == accepted.json()['id'], observed.text
    assert await domain_facts(app) == before and len(provider.requests) == calls
    cancelled = await admin.post(endpoint + '/cancel', headers=auth(observer))
    assert cancelled.status_code == 202, cancelled.text
    terminal = await finish(admin, app, observer, cancelled.json())
    assert terminal['status'] == 'cancelled', terminal
    assert len(provider.requests) == calls


@pytest.mark.parametrize('operation', ['agent_preparation', 'agent_cancel'])
async def test_public_preparation_is_bound_to_original_key_and_agent(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str, operation: str
) -> None:
    owner_app, key, method, endpoint, body, _target = await inference_context(admin, app, provider, model_id, operation)
    observer = await issue(admin, owner_app['id'], scopes=['agents:invoke'], model_ids=key['model_ids'])
    before, calls = await domain_facts(app), len(provider.requests)
    denied = await admin.request(method, endpoint, headers=auth(observer), json=body)
    assert denied.status_code == 404 and 'run' not in denied.json() and 'hits' not in denied.json(), denied.text
    second_agent = await admin.post('/api/agents', json=config(model_id, application_id=owner_app['id']))
    assert second_agent.status_code == 201, second_agent.text
    published = await admin.post(f'/api/agents/{second_agent.json()["id"]}/publish', json={'expected_version': 1})
    assert published.status_code == 200, published.text
    parts = endpoint.split('/')
    parts[3] = second_agent.json()['id']
    mixed = await admin.request(method, '/'.join(parts), headers=auth(key), json=body)
    assert mixed.status_code == 404 and 'run' not in mixed.json() and 'hits' not in mixed.json(), mixed.text
    assert await domain_facts(app) == before and len(provider.requests) == calls
    allowed = await admin.request(method, endpoint, headers=auth(key), json=body)
    assert allowed.status_code == 200, allowed.text


@pytest.mark.parametrize('operation', ['base_rebuild', 'document_rechunk'])
@pytest.mark.parametrize('legacy', [False, True])
async def test_native_rebuild_scope_follows_material_policy(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, operation: str, legacy: bool
) -> None:
    world = await make_world(admin, app, provider, legacy=legacy)
    key = await issue(
        admin, world.application['id'], scopes=['knowledge:write'], model_ids=[world.base['embed_model_id']]
    )
    selected = next(item for item in WRITE_OPERATIONS if item.name == operation)
    before, calls = await domain_facts(app), len(provider.requests)
    response = await admin.request(
        selected.method,
        selected.path.format(**world.values()),
        headers=request_headers(key, etag=etag_for(selected, world, False)),
        json=body_for(selected, world),
    )
    assert response.status_code == (403 if legacy else 202), response.text
    if legacy:
        assert await domain_facts(app) == before
    assert len(provider.requests) == calls


@pytest.mark.parametrize('operation', ['chat', 'embeddings', 'rerank'])
async def test_public_inference_result_keeps_connection_credentials_private(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str, operation: str
) -> None:
    _owner_app, key, method, endpoint, body, target = await inference_context(admin, app, provider, model_id, operation)
    calls = len(provider.requests)
    response = await drive(
        app.state.container,
        asyncio.create_task(
            admin.request(
                method, endpoint, headers={**auth(key), 'X-Auth-Audit-Case': 'authorized-model-result'}, json=body
            )
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()['model'] == target
    assert len(provider.requests) == calls + 1
    assert provider.request_headers[-1]['authorization'] == 'Bearer local-test-key'
    for secret in (
        'local-test-key',
        f'127.0.0.1:{provider.server_port}',
        'api_key',
        'encrypted_api_key',
        'connection_id',
    ):
        assert secret not in response.text, secret
