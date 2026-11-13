"""Real A resources and independent B sessions exercise Console authorization policies.

No external provider is used: model fixtures point only to the local ProviderServer.
The two private-query cancellation cases are regressions for AUTH-PERSONAL-01.
"""

from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.auth_personal_support import identity_client, ids
from tests.core.integration import test_message_actions
from tests.core.integration.test_agents import config
from tests.core.integration.test_knowledge_failures import ready_base, source
from tests.core.integration.test_knowledge_sources import query_result
from tests.core.integration.test_message_actions import create_share
from tests.core.integration.test_skills import install, prepare
from tests.core.integration.test_work import published, task, turn
from tests.core.provider import ProviderServer
from zhiheng.modules.knowledge.orm import RetrievalIntent
from zhiheng.runtime.orm import Run

saved_chat = test_message_actions.saved_chat


@pytest.fixture(autouse=True)
def json_transport(admin: httpx.AsyncClient) -> None:
    admin.headers['content-type'] = 'application/json'


@pytest.mark.parametrize('state', ['pending', 'completed'])
async def test_private_query_cancel_checks_same_access_as_read(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, state: str
) -> None:
    base = await ready_base(admin, app, provider)
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/query',
        json={'query': 'port 8080', 'client_request_id': str(uuid4())},
    )
    assert response.status_code == 202, response.text
    intent = response.json()
    if state == 'completed':
        intent = await query_result(admin, app, base['id'])
        assert intent['status'] == 'completed' and intent['hits'], intent
    path = f'/api/knowledge/preparations/{intent["id"]}'
    async with identity_client(app, admin=True) as other_admin:
        assert (await other_admin.get(f'/api/knowledge/bases/{base["id"]}')).status_code == 404
        assert (await other_admin.get(path)).status_code == 404
        denied = await other_admin.post(path + '/cancel', json={})
        assert denied.status_code == 404, (
            f'private query {state}: unauthorized cancel status={denied.status_code}, body={denied.text}'
        )
    async with app.state.container.database.sessions() as session:
        row = await session.get(RetrievalIntent, UUID(intent['id']))
        assert row is not None and row.status == intent['status']
    assert (await admin.get(path)).status_code == 200
    assert (await admin.post(path + '/cancel', json={})).status_code == 200


@pytest.mark.parametrize('reader_admin', [False, True], ids=['reader', 'reader-admin'])
@pytest.mark.parametrize('state', ['pending', 'completed'])
async def test_shared_query_read_does_not_delegate_another_actors_cancellation(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, reader_admin: bool, state: str
) -> None:
    base = await ready_base(admin, app, provider)
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/query',
        json={'query': 'port', 'client_request_id': str(uuid4())},
    )
    assert response.status_code == 202, response.text
    owned_intent = response.json()
    if state == 'completed':
        owned_intent = await query_result(admin, app, base['id'])
        assert owned_intent['status'] == 'completed' and owned_intent['hits']
    async with identity_client(app, admin=reader_admin) as reader:
        path = f'/api/knowledge/bases/{base["id"]}'
        acl = (await admin.get(path + '/acl')).json()
        invitation = await admin.post(
            path + '/invitations',
            json={'expected_acl_version': acl['base']['acl_version'], 'client_request_id': str(uuid4())},
        )
        assert invitation.status_code == 201, invitation.text
        joined = await reader.post('/api/knowledge/invitations/accept', json={'token': invitation.json()['token']})
        assert joined.status_code == 200, joined.text
        assert (await reader.get(f'/api/knowledge/bases/{base["id"]}')).status_code == 200
        for suffix, method in [('', 'GET'), ('/cancel', 'POST')]:
            denied = await reader.request(
                method,
                f'/api/knowledge/preparations/{owned_intent["id"]}' + suffix,
                **({'json': {}} if method == 'POST' else {}),
            )
            assert denied.status_code == 404, denied.text
        own = await reader.post(
            f'/api/knowledge/bases/{base["id"]}/query',
            json={'query': 'port', 'client_request_id': str(uuid4())},
        )
        assert own.status_code == 202, own.text
        assert (await reader.post(f'/api/knowledge/preparations/{own.json()["id"]}/cancel', json={})).status_code == 200


@pytest.mark.parametrize('actor_admin', [False, True], ids=['member', 'other-admin'])
@pytest.mark.parametrize('operation', ['list', 'available', 'get', 'content', 'confirm', 'status', 'delete'])
async def test_personal_skill_operation_matrix(
    admin: httpx.AsyncClient, app: FastAPI, actor_admin: bool, operation: str
) -> None:
    skill = await install(admin, 'audit-owner-skill', 'Private skill body')
    draft = await prepare(admin, 'audit-draft-skill', 'Private draft body')
    path = f'/api/skills/{skill["id"]}'
    cases = {
        'get': ('GET', path, None),
        'content': ('GET', path + '/content', None),
        'confirm': ('POST', f'/api/skills/import/{draft["draft_id"]}/confirm', {}),
        'status': ('PATCH', path + '/status', {'status': 'disabled', 'expected_version': skill['version']}),
        'delete': ('DELETE', path, {}),
    }
    async with identity_client(app, admin=actor_admin) as stranger:
        if operation in {'list', 'available'}:
            endpoint = '/api/skills' + ('/available' if operation == 'available' else '')
            other = await stranger.get(endpoint)
            assert other.status_code == 200 and skill['id'] not in ids(other.json()), other.text
            assert skill['id'] in ids((await admin.get(endpoint)).json())
        else:
            method, endpoint, body = cases[operation]
            other = await stranger.request(method, endpoint, json=body)
            assert other.status_code == 404, other.text
            assert (await admin.get(path)).json()['id'] == skill['id']
            allowed = await admin.request(method, endpoint, json=body)
            assert allowed.status_code in {200, 201}, allowed.text


@pytest.mark.parametrize('actor_admin', [False, True], ids=['member', 'other-admin'])
@pytest.mark.parametrize(
    'operation',
    ['list', 'detail', 'messages', 'context', 'context-retry', 'run', 'regenerate', 'patch', 'delete'],
)
async def test_private_conversation_operation_matrix(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    saved_chat: dict[str, str],
    actor_admin: bool,
    operation: str,
) -> None:
    base = '/api/chat/conversations/' + saved_chat['conversation_id']
    cases = {
        'detail': ('GET', base, None),
        'messages': ('GET', base + '/messages', None),
        'context': ('GET', base + '/context', None),
        'context-retry': ('POST', base + '/context/retry', {'client_request_id': str(uuid4())}),
        'run': (
            'POST',
            base + '/runs',
            {'model_id': model_id, 'message': 'owner prompt', 'client_request_id': str(uuid4())},
        ),
        'regenerate': (
            'POST',
            base + f'/messages/{saved_chat["message_id"]}/regenerate',
            {'client_request_id': str(uuid4())},
        ),
        'patch': ('PATCH', base, {'expected_version': 1, 'title': 'Changed by owner'}),
        'delete': ('DELETE', base, {'expected_version': 1}),
    }
    async with identity_client(app, admin=actor_admin) as stranger:
        if operation == 'list':
            other = await stranger.get('/api/chat/conversations')
            assert other.status_code == 200 and saved_chat['conversation_id'] not in ids(other.json())
            assert saved_chat['conversation_id'] in ids((await admin.get('/api/chat/conversations')).json())
            return
        method, endpoint, body = cases[operation]
        denied = await stranger.request(method, endpoint, json=body)
        assert denied.status_code == 404, denied.text
        assert (await admin.get(base)).json()['id'] == saved_chat['conversation_id']
        # The saved immutable message fixture has an intentionally empty execution payload.
        # Real regeneration's positive/provider path is covered by test_chat_regeneration.
        if operation != 'regenerate':
            allowed = await admin.request(method, endpoint, json=body)
            assert allowed.status_code in ({200, 409} if operation == 'context-retry' else {200, 202, 204}), (
                allowed.text
            )


@pytest.mark.parametrize('operation', ['feedback', 'feedback-list', 'preview', 'shares', 'share-create', 'revoke'])
async def test_message_actions_reject_other_admin_and_member(
    admin: httpx.AsyncClient, app: FastAPI, saved_chat: dict[str, str], operation: str
) -> None:
    share = await create_share(admin, saved_chat)
    preview = (await admin.get(f'/api/chat/messages/{saved_chat["message_id"]}/share-preview')).json()
    base = '/api/chat/messages/' + saved_chat['message_id']
    cases = {
        'feedback': ('PUT', base + '/feedback', {'value': 'up'}),
        'feedback-list': ('GET', f'/api/chat/conversations/{saved_chat["conversation_id"]}/feedback', None),
        'preview': ('GET', base + '/share-preview', None),
        'shares': ('GET', base + '/shares', None),
        'share-create': ('POST', base + '/shares', {'expected_content_hash': preview['content_hash']}),
        'revoke': ('DELETE', f'/api/chat/shares/{share["id"]}', None),
    }
    method, endpoint, body = cases[operation]
    for is_admin in (False, True):
        async with identity_client(app, admin=is_admin) as stranger:
            denied = await stranger.request(method, endpoint, json=body)
            assert denied.status_code == 404, denied.text
    allowed = await admin.request(method, endpoint, json=body)
    assert allowed.status_code in {200, 201, 204}, allowed.text


@pytest.mark.parametrize('operation', ['snapshot', 'cancel', 'events'])
async def test_generic_run_is_execution_caller_owned(
    admin: httpx.AsyncClient, app: FastAPI, saved_chat: dict[str, str], operation: str
) -> None:
    endpoint = '/api/runs/' + saved_chat['run_id']
    endpoint += {'snapshot': '', 'cancel': '/cancel', 'events': '/events'}[operation]
    method = 'POST' if operation == 'cancel' else 'GET'
    for is_admin in (False, True):
        async with identity_client(app, admin=is_admin) as stranger:
            denied = await stranger.request(method, endpoint)
            assert denied.status_code == 404, denied.text
    allowed = await admin.request(method, endpoint)
    assert allowed.status_code == 200, allowed.text
    if operation == 'events':
        assert 'event: snapshot' in allowed.text


async def test_public_share_never_exposes_private_sources_and_revokes(
    admin: httpx.AsyncClient, app: FastAPI, saved_chat: dict[str, str]
) -> None:
    share = await create_share(admin, saved_chat)
    endpoint = '/api/shared/messages/' + str(share['token'])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        allowed = await anonymous.get(endpoint)
        assert allowed.status_code == 200, allowed.text
        assert set(allowed.json()) == {'question', 'answer', 'created_at', 'expires_at'}
        for secret in ['PRIVATE REASONING', saved_chat['message_id'], saved_chat['owner_id']]:
            assert secret not in allowed.text
        assert (await anonymous.get('/api/chat/conversations/' + saved_chat['conversation_id'])).status_code == 401
        assert (await admin.delete('/api/chat/shares/' + str(share['id']))).status_code == 204
        revoked = await anonymous.get(endpoint)
        assert revoked.status_code == 404 and revoked.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize(
    'operation',
    [
        'get',
        'download',
        'parse',
        'patch',
        'acl',
        'acl-patch',
        'transfer',
        'delete',
        'restore',
        'purge',
        'extend',
        'dispose-expired',
        'references',
        'audit',
        'audit-page',
    ],
)
async def test_private_file_operation_matrix(admin: httpx.AsyncClient, app: FastAPI, operation: str) -> None:
    file = await source(admin, 'audit-private.txt', b'Private file content')
    path = '/api/files/' + file['id']
    async with identity_client(app) as stranger:
        target_id = (await stranger.get('/api/auth/me')).json()['id']
        if operation in {'restore', 'purge'}:
            deleted = await admin.request('DELETE', path, json={'expected_version': file['version']})
            assert deleted.status_code == 200, deleted.text
            file = deleted.json()
        version = file['version']
        cases = {
            'get': ('GET', path, None),
            'download': ('GET', path + '/download', None),
            'parse': ('POST', path + '/parse', {}),
            'patch': ('PATCH', path, {'expected_version': version, 'display_name': 'Updated by owner'}),
            'acl': ('GET', path + '/acl', None),
            'acl-patch': (
                'PATCH',
                path + '/acl',
                {'expected_version': file['acl_version'], 'access': 'private', 'entries': []},
            ),
            'transfer': (
                'POST',
                path + '/transfer',
                {
                    'expected_version': version,
                    'owner_id': target_id,
                    'uploader_access_enabled': False,
                    'reason': 'audit handover',
                },
            ),
            'delete': ('DELETE', path, {'expected_version': version}),
            'restore': ('POST', path + '/restore', {'expected_version': version}),
            'purge': ('POST', path + '/purge', {'expected_version': version}),
            'extend': (
                'POST',
                path + '/extend',
                {'expected_version': version, 'expires_at': '2099-01-01T00:00:00Z', 'reason': 'audit retention'},
            ),
            'dispose-expired': ('POST', path + '/dispose-expired', {'expected_version': version}),
            'references': ('GET', path + '/references', None),
            'audit': ('GET', path + '/audit', None),
            'audit-page': ('GET', path + '/audit/page', None),
        }
        method, endpoint, body = cases[operation]
        denied = await stranger.request(method, endpoint, json=body)
        assert denied.status_code == 404, denied.text
        assert (await admin.get(path)).json()['id'] == file['id']
        allowed = await admin.request(method, endpoint, json=body)
        assert allowed.status_code in ({200, 409} if operation == 'dispose-expired' else {200}), allowed.text


async def test_file_lists_stats_filters_and_explicit_share(admin: httpx.AsyncClient, app: FastAPI) -> None:
    private = await source(admin, 'audit-list-private.txt', b'private')
    owner_id = private['owner_id']
    async with identity_client(app) as stranger:
        own = await source(stranger, 'audit-list-owned.txt', b'owned')
        listed = await stranger.get('/api/files')
        assert listed.status_code == 200 and private['id'] not in ids(listed.json())
        assert own['id'] in ids(listed.json())
        filtered = await stranger.get('/api/files', params={'owner_id': owner_id})
        assert filtered.status_code == 200 and private['id'] not in ids(filtered.json())
        stats = await stranger.get('/api/files/stats', params={'owner_id': owner_id})
        assert stats.status_code == 200 and stats.json()['total_count'] == 0, stats.text
        user_id = (await stranger.get('/api/auth/me')).json()['id']
        granted = await admin.patch(
            f'/api/files/{private["id"]}/acl',
            json={'expected_version': 1, 'access': 'private', 'entries': [{'user_id': user_id, 'effect': 'allow'}]},
        )
        assert granted.status_code == 200, granted.text
        assert private['id'] in ids((await stranger.get('/api/files')).json())
        assert (await stranger.get(f'/api/files/{private["id"]}/download')).content == b'private'
        assert (await stranger.get(f'/api/files/{private["id"]}/acl')).status_code == 404
        assert (
            await stranger.patch(f'/api/files/{private["id"]}', json={'expected_version': 1, 'display_name': 'no'})
        ).status_code == 404
        revoked = await admin.patch(
            f'/api/files/{private["id"]}/acl', json={'expected_version': 2, 'access': 'private', 'entries': []}
        )
        assert revoked.status_code == 200, revoked.text
        assert (await stranger.get(f'/api/files/{private["id"]}/download')).status_code == 404


async def test_file_admin_delegation_preserves_explicit_personal_skill_boundary(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    async with identity_client(app) as owner:
        file = await source(owner, 'audit-admin-delegation.txt', b'private member file')
        path = '/api/files/' + file['id']
        assert (await admin.get(path)).status_code == 404
        assert (await admin.get(path + '/download')).status_code == 404
        patched = await admin.patch(path, json={'expected_version': file['version'], 'display_name': 'Admin updated'})
        assert patched.status_code == 404, patched.text
        assert (await admin.get(path + '/acl')).status_code == 404
        assert (await owner.get(path + '/download')).content == b'private member file'
        skill = await install(owner, 'member-private-skill', 'Private member instruction')
        assert (await admin.get(f'/api/skills/{skill["id"]}/content')).status_code == 404


@pytest.mark.parametrize(
    'operation', ['list', 'available', 'get', 'patch', 'delete', 'publish', 'unpublish', 'preview']
)
async def test_agent_owner_member_and_admin_matrix(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, operation: str
) -> None:
    agent = await published(admin, model_id)
    path = '/api/agents/' + agent['id']
    cases = {
        'get': ('GET', path, None),
        'patch': ('PATCH', path, config(model_id, name='Owner edit', expected_version=agent['version'])),
        'delete': ('DELETE', path, {}),
        'publish': ('POST', path + '/publish', {'expected_version': agent['version']}),
        'unpublish': ('POST', path + '/unpublish', {'expected_version': agent['version']}),
        'preview': (
            'POST',
            path + '/preview',
            {'messages': [{'role': 'user', 'content': 'private preview'}], 'client_request_id': str(uuid4())},
        ),
    }
    async with identity_client(app) as stranger, identity_client(app, admin=True) as administrator:
        if operation in {'list', 'available'}:
            endpoint = '/api/agents' + ('/available' if operation == 'available' else '')
            other = await stranger.get(endpoint)
            assert other.status_code == 200 and agent['id'] not in ids(other.json())
            assert agent['id'] not in ids((await administrator.get(endpoint)).json())
            assert agent['id'] in ids((await admin.get(endpoint)).json())
            return
        method, endpoint, body = cases[operation]
        denied = await stranger.request(method, endpoint, json=body)
        assert denied.status_code == 404, denied.text
        assert (await admin.get(path)).json()['owner_id'] == agent['owner_id']
        assert (await administrator.request(method, endpoint, json=body)).status_code == 404
        allowed = await admin.request(method, endpoint, json=body)
        assert allowed.status_code in {200, 202}, allowed.text
        if operation == 'preview':
            async with app.state.container.database.sessions() as session:
                row = await session.get(Run, UUID(allowed.json()['id']))
                assert row is not None and str(row.billing_owner_id) == agent['owner_id']
                assert str(row.owner_id) == (await admin.get('/api/auth/me')).json()['id']


@pytest.mark.parametrize(
    'operation',
    [
        'list',
        'available',
        'detail',
        'patch',
        'delete',
        'add-member',
        'upgrade-member',
        'remove-member',
        'admit',
        'snapshot',
        'cancel',
        'events',
    ],
)
async def test_work_owner_and_foreign_member_operation_matrix(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, operation: str
) -> None:
    agent = await published(admin, model_id)
    another = await published(admin, model_id, name='Second member')
    owned = await task(admin, agent)
    path = '/api/work/tasks/' + owned['id']
    run_id = ''
    if operation in {'snapshot', 'cancel', 'events'}:
        response = await admin.post(path + '/runs', json=turn(owned, agent))
        assert response.status_code == 202, response.text
        run_id = response.json()['id']
        # A terminal persisted run makes ASGI's finite SSE response deterministic.
        async with app.state.container.database.sessions.begin() as session:
            row = await session.get(Run, UUID(run_id))
            assert row is not None
            row.status = 'completed'
    cases = {
        'detail': ('GET', path, None),
        'patch': ('PATCH', path, {'expected_version': owned['version'], 'title': 'Owner task edit'}),
        'delete': ('DELETE', path + '?expected_version=' + str(owned['version']), None),
        'add-member': ('POST', path + '/members', {'agent_id': another['id'], 'expected_version': owned['version']}),
        'upgrade-member': (
            'PATCH',
            path + '/members/' + agent['id'],
            {'expected_version': owned['version'], 'expected_published_version': agent['published_version']},
        ),
        'remove-member': (
            'DELETE',
            path + '/members/' + agent['id'] + '?expected_version=' + str(owned['version']),
            None,
        ),
        'admit': ('POST', path + '/runs', turn(owned, agent)),
        'snapshot': ('GET', path + '/runs/' + run_id, None),
        'cancel': ('POST', path + '/runs/' + run_id + '/cancel', None),
        'events': ('GET', path + '/runs/' + run_id + '/events', None),
    }
    async with identity_client(app) as stranger:
        if operation in {'list', 'available'}:
            endpoint = '/api/work/tasks' if operation == 'list' else '/api/work/agents/available'
            response = await stranger.get(endpoint)
            target_id = owned['id'] if operation == 'list' else agent['id']
            assert response.status_code == 200 and target_id not in ids(response.json())
            assert target_id in ids((await admin.get(endpoint)).json())
            if operation == 'available':
                assert (await stranger.get(endpoint, params={'task_id': owned['id']})).status_code == 404
            return
        method, endpoint, body = cases[operation]
        denied = await stranger.request(method, endpoint, json=body)
        assert denied.status_code == 404, denied.text
        assert (await admin.get(path)).json()['id'] == owned['id']
        allowed = await admin.request(method, endpoint, json=body)
        assert allowed.status_code in {200, 202, 204}, allowed.text


async def test_secondary_conversation_message_and_work_run_ids_cannot_cross_parents(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, saved_chat: dict[str, str]
) -> None:
    other_conversation = (await admin.post('/api/chat/conversations', json={'title': 'Other parent'})).json()
    response = await admin.post(
        f'/api/chat/conversations/{other_conversation["id"]}/messages/{saved_chat["message_id"]}/regenerate',
        json={'client_request_id': str(uuid4())},
    )
    assert response.status_code == 404, response.text
    assert (await admin.get(f'/api/chat/conversations/{saved_chat["conversation_id"]}/messages')).status_code == 200
    agent = await published(admin, model_id)
    first, second = await task(admin, agent), await task(admin, agent)
    started = await admin.post(f'/api/work/tasks/{first["id"]}/runs', json=turn(first, agent))
    assert started.status_code == 202, started.text
    run_id = started.json()['id']
    assert (await admin.get(f'/api/work/tasks/{first["id"]}/runs/{run_id}')).status_code == 200
    for method, suffix in [('GET', ''), ('POST', '/cancel'), ('GET', '/events')]:
        wrong = await admin.request(method, f'/api/work/tasks/{second["id"]}/runs/{run_id}' + suffix)
        assert wrong.status_code == 404, wrong.text


@pytest.mark.parametrize(
    'operation',
    [
        'get',
        'patch',
        'delete',
        'acl',
        'invite',
        'members',
        'folders',
        'folder-create',
        'folder-patch',
        'folder-delete',
        'upload',
        'upload-delete',
        'documents',
        'import',
        'rechunk',
        'preview',
        'query',
        'jobs',
        'job',
        'job-cancel',
        'rebuild',
        'document-delete',
    ],
)
async def test_knowledge_owner_only_management_and_private_read_matrix(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, operation: str
) -> None:
    base = await ready_base(admin, app, provider)
    path = '/api/knowledge/bases/' + base['id']
    base = (await admin.get(path)).json()
    document = (await admin.get(path + '/documents')).json()['items'][0]
    folder_response = await admin.post(path + '/folders', json={'name': 'Owned folder'})
    assert folder_response.status_code == 201, folder_response.text
    folder = folder_response.json()
    staged = await admin.post(
        path + '/uploads',
        params={'filename': 'staged.txt'},
        content=b'Staged private data',
        headers={'content-type': 'application/octet-stream'},
    )
    assert staged.status_code == 201, staged.text
    job = (await admin.get(path + '/jobs')).json()[0]
    job_body = {'client_request_id': str(uuid4()), 'expected_version': base['version']}
    cases = {
        'get': ('GET', path, None),
        'patch': ('PATCH', path, {'expected_version': base['version'], 'name': 'Owner edit'}),
        'delete': ('DELETE', path, job_body),
        'acl': ('GET', path + '/acl', None),
        'invite': (
            'POST',
            path + '/invitations',
            {'expected_acl_version': base['acl_version'], 'client_request_id': str(uuid4())},
        ),
        'members': ('GET', path + '/members', None),
        'folders': ('GET', path + '/folders', None),
        'folder-create': ('POST', path + '/folders', {'name': 'New folder'}),
        'folder-patch': (
            'PATCH',
            path + '/folders/' + folder['id'],
            {'expected_version': folder['version'], 'name': 'Rename'},
        ),
        'folder-delete': (
            'DELETE',
            path + '/folders/' + folder['id'],
            {'client_request_id': str(uuid4()), 'expected_version': folder['version']},
        ),
        'upload': ('POST', path + '/uploads?filename=owned-upload.txt', b'Owned new upload'),
        'upload-delete': ('DELETE', path + '/uploads/' + staged.json()['upload_id'], {}),
        'documents': ('GET', path + '/documents', None),
        'import': (
            'POST',
            path + '/documents',
            {'file_ids': [staged.json()['file']['id']], 'client_request_id': str(uuid4())},
        ),
        'rechunk': (
            'POST',
            path + '/documents/' + document['id'] + '/rechunk',
            {'client_request_id': str(uuid4()), 'expected_version': document['version']},
        ),
        'preview': ('GET', path + '/documents/' + document['id'] + '/preview', None),
        'query': ('POST', path + '/query', {'query': 'port', 'client_request_id': str(uuid4())}),
        'jobs': ('GET', path + '/jobs', None),
        'job': ('GET', '/api/knowledge/jobs/' + job['id'], None),
        'job-cancel': ('POST', '/api/knowledge/jobs/' + job['id'] + '/cancel', {}),
        'rebuild': ('POST', path + '/rebuild', job_body),
        'document-delete': (
            'DELETE',
            path + '/documents/' + document['id'],
            {'client_request_id': str(uuid4()), 'expected_version': document['version']},
        ),
    }
    method, endpoint, body = cases[operation]
    kwargs = (
        {'content': body, 'headers': {'content-type': 'application/octet-stream'}}
        if isinstance(body, bytes)
        else {'json': body}
    )
    for is_admin in (False, True):
        async with identity_client(app, admin=is_admin) as stranger:
            denied = await stranger.request(method, endpoint, **kwargs)
            assert denied.status_code == 404, denied.text
    assert (await admin.get(path)).json()['id'] == base['id']
    allowed = await admin.request(method, endpoint, **kwargs)
    assert allowed.status_code in {200, 201, 202, 204}, allowed.text


async def test_link_membership_reads_library_copies_without_exposing_original_files(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from zhiheng.modules.knowledge.orm import KnowledgeDocument

    base = await ready_base(admin, app, provider)
    path = '/api/knowledge/bases/' + base['id']
    document = (await admin.get(path + '/documents')).json()['items'][0]
    async with app.state.container.database.sessions() as session:
        row = await session.get(KnowledgeDocument, UUID(document['id']))
        original_id = row.origin_file_id
    assert original_id is not None
    async with identity_client(app) as reader:
        assert base['id'] not in ids((await reader.get('/api/knowledge/bases')).json())
        reader_id = (await reader.get('/api/auth/me')).json()['id']
        acl = (await admin.get(path + '/acl')).json()
        invitation = await admin.post(
            path + '/invitations',
            json={'expected_acl_version': acl['base']['acl_version'], 'client_request_id': str(uuid4())},
        )
        assert invitation.status_code == 201, invitation.text
        joined = await reader.post('/api/knowledge/invitations/accept', json={'token': invitation.json()['token']})
        assert joined.status_code == 200, joined.text
        assert document['id'] in ids((await reader.get(path + '/documents')).json())
        assert (await reader.get(path + '/documents/' + document['id'] + '/preview')).status_code == 200
        assert (await reader.get('/api/files/' + str(original_id))).status_code == 404
        for suffix in ['/acl', '/users', '/jobs']:
            assert (await reader.get(path + suffix)).status_code == 404
        acl = (await admin.get(path + '/acl')).json()
        revoked = await admin.request(
            'DELETE', path + '/members/' + reader_id, json={'expected_acl_version': acl['base']['acl_version']}
        )
        assert revoked.status_code == 204, revoked.text
        assert (await reader.get(path)).status_code == 404


@pytest.mark.parametrize('child', ['folder', 'upload', 'document-preview', 'document-rechunk', 'document-delete'])
async def test_knowledge_secondary_ids_require_real_parent_match(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, child: str
) -> None:
    base = await ready_base(admin, app, provider)
    first = '/api/knowledge/bases/' + base['id']
    other = await admin.post(
        '/api/knowledge/bases', json={'name': 'Other owner parent', 'embed_model_id': base['embed_model_id']}
    )
    assert other.status_code == 201, other.text
    second = '/api/knowledge/bases/' + other.json()['id']
    folder = (await admin.post(first + '/folders', json={'name': 'Actual child'})).json()
    staged = await admin.post(
        first + '/uploads?filename=actual-child.txt',
        content=b'Actual child',
        headers={'content-type': 'application/octet-stream'},
    )
    assert staged.status_code == 201, staged.text
    document = (await admin.get(first + '/documents')).json()['items'][0]
    cases = {
        'folder': (
            'PATCH',
            '/folders/' + folder['id'],
            {'name': 'wrong parent', 'expected_version': folder['version']},
        ),
        'upload': ('DELETE', '/uploads/' + staged.json()['upload_id'], {}),
        'document-preview': ('GET', '/documents/' + document['id'] + '/preview', None),
        'document-rechunk': (
            'POST',
            '/documents/' + document['id'] + '/rechunk',
            {'client_request_id': str(uuid4()), 'expected_version': document['version']},
        ),
        'document-delete': (
            'DELETE',
            '/documents/' + document['id'],
            {'client_request_id': str(uuid4()), 'expected_version': document['version']},
        ),
    }
    method, suffix, body = cases[child]
    denied = await admin.request(method, second + suffix, json=body)
    assert denied.status_code == 404, denied.text
    assert (await admin.get(first + '/documents')).json()['items'][0]['id'] == document['id']
    assert (await admin.get(first + '/folders')).json()[0]['id'] == folder['id']


@pytest.mark.parametrize(
    'operation', ['config', 'config-patch', 'build', 'preview', 'query', 'jobs', 'job', 'cancel', 'labels', 'subgraph']
)
async def test_graph_private_base_and_persisted_job_operation_matrix(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str, operation: str
) -> None:
    from tests.core.auth_personal_support import graph_inputs

    base, job = await graph_inputs(admin, app, provider, model_id)
    path = f'/api/knowledge/bases/{base["id"]}/graph'
    cases = {
        'config': ('GET', path + '/config', None),
        'config-patch': (
            'PATCH',
            path + '/config',
            {'expected_version': 1, 'llm_model_id': model_id, 'max_output_tokens': 1024},
        ),
        'build': ('POST', path + '/build', {'client_request_id': str(uuid4()), 'expected_version': 1}),
        'preview': ('POST', path + '/preview', {'client_request_id': str(uuid4()), 'expected_version': 1}),
        'query': ('POST', path + '/query', {'client_request_id': str(uuid4()), 'query': 'port'}),
        'jobs': ('GET', path + '/jobs', None),
        'job': ('GET', path + '/jobs/' + job['id'], None),
        'cancel': ('POST', path + '/jobs/' + job['id'] + '/cancel', {}),
        'labels': ('GET', path + '/labels', None),
        'subgraph': ('GET', path + '/subgraph', None),
    }
    method, endpoint, body = cases[operation]
    for is_admin in (False, True):
        async with identity_client(app, admin=is_admin) as stranger:
            denied = await stranger.request(method, endpoint, json=body)
            assert denied.status_code == 404, denied.text
    assert (await admin.get(path + '/jobs/' + job['id'])).status_code == 200
    allowed = await admin.request(method, endpoint, json=body)
    expected = (
        {409} if operation in {'labels', 'subgraph'} else {503} if operation in {'build', 'preview', 'query'} else {200}
    )
    assert allowed.status_code in expected, allowed.text
    # SDK availability failures are recorded separately from successful execution;
    # the authorization proof is the real base/job plus foreign denial above.


@pytest.mark.parametrize('operation', ['get', 'cancel'])
async def test_graph_job_secondary_id_cannot_cross_existing_bases(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str, operation: str
) -> None:
    from tests.core.auth_personal_support import graph_inputs

    base, job = await graph_inputs(admin, app, provider, model_id)
    other = await admin.post(
        '/api/knowledge/bases', json={'name': 'Other graph parent', 'embed_model_id': base['embed_model_id']}
    )
    assert other.status_code == 201, other.text
    suffix = '/cancel' if operation == 'cancel' else ''
    method = 'POST' if operation == 'cancel' else 'GET'
    path = f'/api/knowledge/bases/{other.json()["id"]}/graph/jobs/{job["id"]}' + suffix
    wrong = await admin.request(method, path, json={} if operation == 'cancel' else None)
    assert wrong.status_code == 404, wrong.text
    assert (await admin.get(f'/api/knowledge/bases/{base["id"]}/graph/jobs/{job["id"]}')).status_code == 200


@pytest.mark.parametrize(
    'operation',
    [
        'users',
        'policy',
        'policies',
        'jobs',
        'issues',
        'issue-stats',
        'policy-write',
        'retention-create',
        'retention-patch',
        'retention-delete',
        'run-job',
        'issue-action',
    ],
)
async def test_storage_governance_permissions_do_not_grant_private_file_access(
    admin: httpx.AsyncClient, app: FastAPI, operation: str
) -> None:
    from tests.core.integration.test_ai_management_permissions import account
    from zhiheng.modules.files.orm import FileJob, FileReconcileIssue, FileRecord

    file = await source(admin, 'audit-governed.txt', b'Owned private file')
    created = await admin.post('/api/files/policies', json={'resource_type': 'audit_matrix', 'retention_days': 30})
    assert created.status_code == 201, created.text
    async with app.state.container.database.sessions.begin() as session:
        file_row = await session.get(FileRecord, UUID(file['id']))
        assert file_row is not None
        job = FileJob(kind='reconcile', status='succeeded')
        session.add(job)
        await session.flush()
        issue = FileReconcileIssue(
            job_id=job.id,
            file_id=file_row.id,
            identity_key='audit-' + uuid4().hex,
            storage_key=file_row.storage_key,
            kind='missing',
            detail='Audit fixture issue',
        )
        session.add(issue)
        await session.flush()
        issue_id = str(issue.id)
    cases = {
        'users': ('GET', '/api/files/users', None),
        'policy': ('GET', '/api/files/policy', None),
        'policies': ('GET', '/api/files/policies', None),
        'jobs': ('GET', '/api/files/jobs', None),
        'issues': ('GET', '/api/files/reconcile/issues', None),
        'issue-stats': ('GET', '/api/files/reconcile/stats', None),
        'policy-write': (
            'PATCH',
            '/api/files/policy',
            {'retention_days': 30, 'reminder_days': 7, 'expected_version': 1},
        ),
        'retention-create': ('POST', '/api/files/policies', {'resource_type': 'audit_second', 'retention_days': 31}),
        'retention-patch': ('PATCH', '/api/files/policies/audit_matrix', {'retention_days': 31, 'expected_version': 1}),
        'retention-delete': ('DELETE', '/api/files/policies/audit_matrix', {'expected_version': 1}),
        'run-job': ('POST', '/api/files/jobs/reconcile', {}),
        'issue-action': (
            'POST',
            f'/api/files/reconcile/issues/{issue_id}/actions',
            {'action': 'ignore', 'expected_version': 1},
        ),
    }
    method, endpoint, body = cases[operation]
    async with account(admin, app) as (stranger, _, _):
        denied = await stranger.request(method, endpoint, json=body)
        assert denied.status_code == 403, denied.text
    async with account(admin, app, 'ai:storage:read') as (reader, _, _):
        response = await reader.request(method, endpoint, json=body)
        expected = 200 if method == 'GET' and operation != 'users' else 403
        assert response.status_code == expected, response.text
        assert (await reader.get('/api/files/' + file['id'])).status_code == 404
    async with account(admin, app, 'ai:storage:write') as (writer, _, _):
        allowed = await writer.request(method, endpoint, json=body)
        assert allowed.status_code in ({403} if operation == 'users' else {200, 201, 204}), allowed.text
        assert (await writer.get('/api/files/' + file['id'] + '/download')).status_code == 404

    if operation == 'users':
        async with account(admin, app, 'ai:storage:read', 'system:users:read') as (directory_reader, _, _):
            assert (await directory_reader.get(endpoint)).status_code == 200
            assert (await directory_reader.get('/api/files/' + file['id'])).status_code == 404


async def test_file_reminders_follow_current_file_owner(admin: httpx.AsyncClient, app: FastAPI) -> None:
    from datetime import timedelta

    from zhiheng.modules.files.orm import FileRecord, FileReminder
    from zhiheng.shared.time import utcnow

    file = await source(admin, 'audit-reminder.txt', b'Owned reminder')
    expires = utcnow() + timedelta(days=1)
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(FileRecord, UUID(file['id']))
        assert row is not None
        row.expires_at = expires
        reminder = FileReminder(file_id=row.id, owner_id=row.owner_id, kind='expiry', expiry_key=expires.isoformat())
        session.add(reminder)
        await session.flush()
        reminder_id = str(reminder.id)
    async with identity_client(app) as stranger:
        response = await stranger.get('/api/files/reminders')
        assert response.status_code == 200 and reminder_id not in ids(response.json())
        assert (await stranger.post(f'/api/files/reminders/{reminder_id}/read', json={})).status_code == 404
    assert reminder_id in ids((await admin.get('/api/files/reminders')).json())
    assert (await admin.post(f'/api/files/reminders/{reminder_id}/read', json={})).json()['is_read'] is True


@pytest.mark.parametrize('origin', ['chat', 'work'])
async def test_sse_reauthenticates_before_later_snapshots_without_starting_server(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, origin: str
) -> None:
    import asyncio

    from sqlalchemy import text
    from starlette.requests import Request

    from tests.core.integration.test_chat_runs import admit
    from zhiheng.api.console import runs as run_routes
    from zhiheng.api.console import work as work_routes

    container = app.state.container
    if origin == 'chat':
        _, run = await admit(admin, model_id)
        resource_id = None
    else:
        agent = await published(admin, model_id)
        owned = await task(admin, agent)
        response = await admin.post(f'/api/work/tasks/{owned["id"]}/runs', json=turn(owned, agent))
        assert response.status_code == 202, response.text
        run, resource_id = response.json(), UUID(owned['id'])
    user_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    async with container.database.sessions() as session:
        principal = await container.identity.principal_for_user(session, user_id)

    async def receive() -> dict:
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    request = Request(
        {
            'type': 'http',
            'method': 'GET',
            'path': '/',
            'query_string': b'',
            'headers': [(b'cookie', ('zhiheng_session=' + admin.cookies.get('zhiheng_session')).encode())],
        },
        receive=receive,
    )
    if origin == 'chat':
        response = await run_routes.events(UUID(run['id']), request, container, principal)
    else:
        response = await work_routes.events(resource_id, UUID(run['id']), request, container, principal)
    iterator = response.body_iterator
    assert 'event: snapshot' in await asyncio.wait_for(anext(iterator), timeout=2)
    async with container.database.sessions.begin() as session:
        await session.execute(text('UPDATE identity_users SET is_active=false WHERE id=:id'), {'id': user_id})
        await session.execute(
            text('UPDATE runtime_runs SET text=:secret, revision=revision+1 WHERE id=:id'),
            {'id': UUID(run['id']), 'secret': 'never deliver after access loss'},
        )
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(iterator), timeout=2)
