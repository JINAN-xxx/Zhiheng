from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_knowledge import VectorFixture, embedding_model
from tests.core.integration.test_knowledge_failures import finish_job
from tests.core.provider import ProviderServer
from zhiheng.modules.files.orm import FileRecord, FileReference
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeDocument, KnowledgeGrant
from zhiheng.shared.errors import AppError


async def library(admin: httpx.AsyncClient, provider: ProviderServer, name: str = 'Library') -> dict[str, Any]:
    model = await embedding_model(admin, provider)
    response = await admin.post('/api/knowledge/bases', json={'name': name, 'embed_model_id': model})
    assert response.status_code == 201, response.text
    assert response.json()['document_policy'] == 'library'
    return response.json()


async def sent_source(admin: httpx.AsyncClient, app: FastAPI, filename: str = 'sent.txt') -> dict[str, Any]:
    response = await admin.post(
        '/api/chat/attachments',
        params={'filename': filename},
        content=b'private project 8080',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert response.status_code == 201, response.text
    info = response.json()
    assert info['purpose'] == 'chat_attachment' and info['expires_at'] is not None
    async with app.state.container.database.sessions.begin() as session:
        # The real chat admission is the only production writer of these business references.
        session.add(FileReference(file_id=UUID(info['id']), resource_type='chat', resource_id=uuid4()))
        row = await session.get(FileRecord, UUID(info['id']))
        row.expires_at = None
    return info


async def reader(app: FastAPI) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
    response = await client.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
    assert response.status_code == 200, response.text
    return client


async def share(admin: httpx.AsyncClient, base: dict[str, Any], member: httpx.AsyncClient) -> dict[str, Any]:
    path = f'/api/knowledge/bases/{base["id"]}'
    invitation = await admin.post(
        path + '/invitations', json={'expected_acl_version': base['acl_version'], 'client_request_id': str(uuid4())}
    )
    assert invitation.status_code == 201, invitation.text
    accepted = await member.post('/api/knowledge/invitations/accept', json={'token': invitation.json()['token']})
    assert accepted.status_code == 200, accepted.text
    return (await admin.get(path)).json()


async def remove_share(admin: httpx.AsyncClient, base: dict[str, Any], member: httpx.AsyncClient) -> httpx.Response:
    member_id = (await member.get('/api/auth/me')).json()['id']
    return await admin.request(
        'DELETE',
        f'/api/knowledge/bases/{base["id"]}/members/{member_id}',
        json={'expected_acl_version': base['acl_version']},
    )


async def test_chat_add_copies_deduplicates_and_follows_current_library_acl(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await library(admin, provider)
    source = await sent_source(admin, app)
    assert (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['total'] == 0
    member = await reader(app)
    try:
        base = await share(admin, base, member)
        response = await admin.post(
            f'/api/knowledge/bases/{base["id"]}/attachments',
            json={
                'file_id': source['id'],
                'client_request_id': str(uuid4()),
            },
        )
        assert response.status_code == 202, response.text
        result = response.json()
        document = result['document']
        assert document['file_id'] != source['id']
        assert not result['already_exists']
        repeated = await admin.post(
            f'/api/knowledge/bases/{base["id"]}/attachments',
            json={
                'file_id': source['id'],
                'client_request_id': str(uuid4()),
            },
        )
        assert repeated.status_code == 202 and repeated.json()['already_exists'], repeated.text
        assert repeated.json()['document']['id'] == document['id']
        assert repeated.json()['job']['id'] == result['job']['id']
        assert (await member.get(f'/api/files/{source["id"]}/content')).status_code == 404
        path = f'/api/knowledge/bases/{base["id"]}/documents/{document["id"]}'
        preview = await member.get(path + '/preview')
        assert preview.status_code == 200 and preview.json()['text'] == 'private project 8080', preview.text
        assert (await member.get(path + '/download')).content == b'private project 8080'
        assert (
            await member.post(
                f'/api/knowledge/bases/{base["id"]}/attachments',
                json={
                    'file_id': source['id'],
                    'client_request_id': str(uuid4()),
                },
            )
        ).status_code == 404
        other = await library(admin, provider, 'Other')
        copied = await admin.post(
            f'/api/knowledge/bases/{other["id"]}/attachments',
            json={
                'file_id': source['id'],
                'client_request_id': str(uuid4()),
            },
        )
        assert copied.status_code == 202, copied.text
        assert copied.json()['document']['file_id'] not in {source['id'], document['file_id']}
        # Losing the original chat file does not invalidate either independent copy.
        async with app.state.container.database.sessions.begin() as session:
            original = await session.get(FileRecord, UUID(source['id']))
            original.status = 'deleted'
        assert (await member.get(path + '/download')).status_code == 200
        revoked = await remove_share(admin, base, member)
        assert revoked.status_code == 204, revoked.text
        for endpoint in (path + '/preview', path + '/download', f'/api/files/{document["file_id"]}/content'):
            assert (await member.get(endpoint)).status_code == 404, endpoint
        async with app.state.container.database.sessions() as session:
            assert (
                len(
                    list(
                        await session.scalars(
                            select(KnowledgeDocument).where(KnowledgeDocument.base_id == UUID(base['id']))
                        )
                    )
                )
                == 1
            )
    finally:
        await member.aclose()


async def test_unsent_or_foreign_attachment_cannot_be_added(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await library(admin, provider)
    source = (
        await admin.post(
            '/api/files?filename=draft.txt', content=b'draft', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/attachments',
        json={
            'file_id': source['id'],
            'client_request_id': str(uuid4()),
        },
    )
    assert response.status_code == 422 and response.json()['error']['code'] == 'attachment_not_sent', response.text


async def test_confirmed_delete_discloses_only_counts_and_tombstones_before_cleanup(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await library(admin, provider)
    app.state.container.knowledge.base_references = AsyncMock(return_value={'items': [], 'hidden_count': 2})
    preview = await admin.get(f'/api/knowledge/bases/{base["id"]}/references')
    assert preview.json() == {'items': [], 'hidden_count': 2, 'total': 2}
    body = {'expected_version': base['version'], 'client_request_id': str(uuid4())}
    blocked = await admin.request('DELETE', f'/api/knowledge/bases/{base["id"]}', json=body)
    assert blocked.status_code == 409, blocked.text
    response = await admin.request(
        'DELETE',
        f'/api/knowledge/bases/{base["id"]}',
        json={
            **body,
            'client_request_id': str(uuid4()),
            'confirm_references': True,
        },
    )
    assert response.status_code == 202, response.text
    assert (await admin.get(f'/api/knowledge/bases/{base["id"]}')).status_code == 404


async def test_legacy_library_changes_permission_only_after_complete_migration(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await library(admin, provider)
    container = app.state.container
    container.knowledge_jobs.vectors = VectorFixture()
    async with container.database.sessions.begin() as session:
        row = await session.get(KnowledgeBase, UUID(base['id']))
        row.document_policy = 'legacy'
    source = (
        await admin.post(
            '/api/files?filename=legacy.txt',
            content=b'legacy material',
            headers={'Content-Type': 'application/octet-stream'},
        )
    ).json()
    queued = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={
            'file_ids': [source['id']],
            'client_request_id': str(uuid4()),
        },
    )
    assert queued.status_code == 202, queued.text
    await finish_job(admin, app, queued.json())
    base = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
    old_revision = base['active_revision']
    member = await reader(app)
    try:
        async with app.state.container.database.sessions.begin() as session:
            member_id = UUID((await member.get('/api/auth/me')).json()['id'])
            session.add(KnowledgeGrant(base_id=UUID(base['id']), user_id=member_id, effect='allow'))
        base = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
        preview = await admin.get(f'/api/knowledge/bases/{base["id"]}/migration')
        assert preview.status_code == 200 and preview.json()['can_migrate'], preview.text
        before = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items'][0]
        path = f'/api/knowledge/bases/{base["id"]}/documents/{before["id"]}/download'
        assert (await member.get(path)).status_code == 404
        body = {
            'expected_version': base['version'],
            'expected_acl_version': base['acl_version'],
            'confirm_shared_access': True,
            'client_request_id': str(uuid4()),
        }
        queued = await admin.post(f'/api/knowledge/bases/{base["id"]}/migration', json=body)
        assert queued.status_code == 202, queued.text
        pending = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
        assert pending['document_policy'] == 'legacy' and pending['active_revision'] == old_revision
        assert (await member.get(path)).status_code == 404
        result = await finish_job(admin, app, queued.json())
        assert result['status'] == 'completed', result
        migrated = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
        assert migrated['document_policy'] == 'library' and migrated['active_revision'] != old_revision
        documents = (await member.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items']
        assert len(documents) == 1 and documents[0]['file_id'] != source['id']
        assert (
            await member.get(f'/api/knowledge/bases/{base["id"]}/documents/{documents[0]["id"]}/download')
        ).content == b'legacy material'
        replay = await admin.post(f'/api/knowledge/bases/{base["id"]}/migration', json=body)
        assert replay.status_code == 202 and replay.json()['id'] == queued.json()['id'], replay.text
    finally:
        await member.aclose()


async def test_real_private_agent_reference_does_not_block_owner_delete(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
) -> None:
    from tests.core.integration.test_agents import config

    base = await library(admin, provider)
    app.state.container.knowledge_jobs.vectors = VectorFixture()
    source = await sent_source(admin, app)
    added = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/attachments',
        json={
            'file_id': source['id'],
            'client_request_id': str(uuid4()),
        },
    )
    await finish_job(admin, app, added.json()['job'])
    base = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
    member = await reader(app)
    try:
        base = await share(admin, base, member)
        created = await member.post(
            '/api/agents',
            json=config(
                model_id,
                name='MEMBER PRIVATE SECRET',
                knowledge_base_ids=[base['id']],
            ),
        )
        assert created.status_code == 201, created.text
        agent = created.json()
        impact = await admin.get(f'/api/knowledge/bases/{base["id"]}/references')
        assert impact.json()['hidden_count'] >= 1 and impact.json()['items'] == [], impact.text
        assert 'MEMBER PRIVATE SECRET' not in impact.text and agent['id'] not in impact.text
        result = await admin.request(
            'DELETE',
            f'/api/knowledge/bases/{base["id"]}',
            json={
                'expected_version': base['version'],
                'client_request_id': str(uuid4()),
                'confirm_references': True,
            },
        )
        assert result.status_code == 202, result.text
        retained = await member.get('/api/agents/' + agent['id'])
        assert retained.status_code == 200 and retained.json()['knowledge_base_ids'] == [base['id']]
        before = len(provider.requests)
        invoke = await member.post(
            '/api/agents/' + agent['id'] + '/preview',
            json={
                'messages': [{'role': 'user', 'content': 'question'}],
                'client_request_id': str(uuid4()),
            },
        )
        assert invoke.status_code in {404, 409}, invoke.text
        assert len(provider.requests) == before
    finally:
        await member.aclose()


async def test_migration_acl_change_keeps_legacy_generation_and_can_retry(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base = await library(admin, provider)
    container = app.state.container
    container.knowledge_jobs.vectors = VectorFixture()
    async with container.database.sessions.begin() as session:
        (await session.get(KnowledgeBase, UUID(base['id']))).document_policy = 'legacy'
        member_id = await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'member'))
        session.add(KnowledgeGrant(base_id=UUID(base['id']), user_id=member_id, effect='allow'))
    original = (
        await admin.post(
            '/api/files?filename=old.txt', content=b'old source', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()
    imported = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={
            'file_ids': [original['id']],
            'client_request_id': str(uuid4()),
        },
    )
    await finish_job(admin, app, imported.json())
    base = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
    body = {
        'expected_version': base['version'],
        'expected_acl_version': base['acl_version'],
        'confirm_shared_access': True,
        'client_request_id': str(uuid4()),
    }
    queued = await admin.post(f'/api/knowledge/bases/{base["id"]}/migration', json=body)
    assert queued.status_code == 202, queued.text
    changed = await admin.request(
        'DELETE',
        f'/api/knowledge/bases/{base["id"]}/members/{member_id}',
        json={'expected_acl_version': base['acl_version']},
    )
    assert changed.status_code == 204, changed.text
    failed = await finish_job(admin, app, queued.json())
    assert failed['status'] == 'failed', failed
    retained = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
    assert retained['document_policy'] == 'legacy' and retained['active_revision'] == base['active_revision']
    documents = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items']
    assert len(documents) == 1 and documents[0]['file_id'] == original['id']
    retry = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/migration',
        json={
            'expected_version': retained['version'],
            'expected_acl_version': retained['acl_version'],
            'confirm_shared_access': True,
            'client_request_id': str(uuid4()),
        },
    )
    assert retry.status_code == 202, retry.text
    assert (await finish_job(admin, app, retry.json()))['status'] == 'completed'


async def test_chat_drafts_expire_after_24h_but_sent_and_referenced_sources_are_retained(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
) -> None:
    from datetime import datetime, timedelta

    from zhiheng.shared.time import utcnow

    draft = await admin.post(
        '/api/chat/attachments?filename=draft.txt',
        content=b'draft',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert draft.status_code == 201, draft.text
    file = draft.json()
    assert utcnow() + timedelta(hours=23) < datetime.fromisoformat(file['expires_at']) < utcnow() + timedelta(hours=25)
    conversation = (await admin.post('/api/chat/conversations', json={})).json()
    sent = await admin.post(
        f'/api/chat/conversations/{conversation["id"]}/runs',
        json={
            'model_id': model_id,
            'message': 'read this',
            'file_ids': [file['id']],
            'client_request_id': str(uuid4()),
        },
    )
    assert sent.status_code == 202, sent.text
    container = app.state.container
    async with container.database.sessions() as session:
        source = await session.get(FileRecord, UUID(file['id']))
        assert source.expires_at is None
        artifacts = list(await session.scalars(select(FileRecord).where(FileRecord.source_file_id == source.id)))
        assert artifacts and all(artifact.expires_at is None for artifact in artifacts)
    expired_ids = []
    for filename in ('abandoned.txt', 'referenced.txt'):
        response = await admin.post(
            '/api/chat/attachments',
            params={'filename': filename},
            content=b'abandoned',
            headers={'Content-Type': 'application/octet-stream'},
        )
        expired_ids.append(UUID(response.json()['id']))
    async with container.database.sessions.begin() as session:
        for file_id in expired_ids:
            (await session.get(FileRecord, file_id)).expires_at = utcnow() - timedelta(seconds=1)
        session.add(FileReference(file_id=expired_ids[1], resource_type='knowledge_job', resource_id=uuid4()))
    response = await admin.post('/api/files/jobs/expiry', json={})
    assert response.status_code == 200, response.text
    async with container.database.sessions() as session:
        assert (await session.get(FileRecord, expired_ids[0])).status == 'deleted'
        assert (await session.get(FileRecord, expired_ids[1])).status == 'active'


async def test_library_upload_uses_base_chunk_defaults_and_shared_users_cannot_change_acl(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base = await library(admin, provider)
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(KnowledgeBase, UUID(base['id']))
        row.chunk_size, row.chunk_overlap = 640, 80
    upload = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/uploads?filename=normal.txt',
        content=b'content',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert upload.status_code == 201, upload.text
    imported = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={
            'file_ids': [upload.json()['file']['id']],
            'client_request_id': str(uuid4()),
        },
    )
    assert imported.status_code == 202, imported.text
    document = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items'][0]
    assert (document['chunk_size'], document['chunk_overlap']) == (640, 80)
    member = await reader(app)
    try:
        base = await share(admin, base, member)
        changed = await member.patch(
            f'/api/knowledge/bases/{base["id"]}/acl',
            json={
                'expected_version': base['acl_version'],
                'access': 'public',
                'entries': [],
            },
        )
        assert changed.status_code == 405
    finally:
        await member.aclose()


async def test_legacy_denies_and_expiry_require_explicit_acl_cleanup_before_migration(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base = await library(admin, provider)
    async with app.state.container.database.sessions.begin() as session:
        (await session.get(KnowledgeBase, UUID(base['id']))).document_policy = 'legacy'
    async with app.state.container.database.sessions.begin() as session:
        member_id = await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'member'))
        session.add(KnowledgeGrant(base_id=UUID(base['id']), user_id=member_id, effect='deny'))
    preview = await admin.get(f'/api/knowledge/bases/{base["id"]}/migration')
    assert preview.json()['requires_acl_cleanup'] and not preview.json()['can_migrate']
    blocked = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/migration',
        json={
            'expected_version': base['version'],
            'expected_acl_version': base['acl_version'],
            'confirm_shared_access': True,
            'client_request_id': str(uuid4()),
        },
    )
    assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'knowledge_migration_blocked'
    assert (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()['document_policy'] == 'legacy'


async def test_resource_shared_reader_uses_kb_grant_for_copy_and_revocation_is_immediate(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    from tests.core.integration.test_resource_key_scopes import application, issue

    base = await library(admin, provider)
    source = await sent_source(admin, app)
    added = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/attachments',
        json={
            'file_id': source['id'],
            'client_request_id': str(uuid4()),
        },
    )
    document = added.json()['document']
    member = await reader(app)
    try:
        base = await share(admin, base, member)
        owner_app = await application(member)
        key = await issue(member, owner_app['id'], scopes=['knowledge:read'])
        container = app.state.container
        async with container.database.sessions.begin() as session:
            caller = await container.resource_access.bearer(session, key['api_key'], scope='knowledge:read')
            await container.resource_access.bind(
                session,
                caller.principal,
                UUID(owner_app['id']),
                'knowledge_base',
                UUID(base['id']),
                ['read'],
                expected_version=1,
            )
        headers = {'Authorization': 'Bearer ' + key['api_key']}
        path = f'/api/v1/knowledge-bases/{base["id"]}/documents/{document["id"]}'
        preview = await member.get(path + '/preview', headers=headers)
        assert preview.status_code == 200 and preview.json()['text'] == 'private project 8080', preview.text
        downloaded = await member.get(path + '/download', headers=headers)
        assert downloaded.status_code == 200 and downloaded.content == b'private project 8080', downloaded.text
        revoked = await remove_share(admin, base, member)
        assert revoked.status_code == 204
        assert (await member.get(path + '/preview', headers=headers)).status_code == 404
        assert (await member.get(path + '/download', headers=headers)).status_code == 404
    finally:
        await member.aclose()


async def test_removing_material_schedules_only_its_copy_for_platform_retention(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    from zhiheng.shared.time import utcnow

    base = await library(admin, provider)
    container = app.state.container
    container.knowledge_jobs.vectors = VectorFixture()
    original = await sent_source(admin, app)
    added = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/attachments',
        json={
            'file_id': original['id'],
            'client_request_id': str(uuid4()),
        },
    )
    document = added.json()['document']
    await finish_job(admin, app, added.json()['job'])
    current = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items'][0]
    removed = await admin.request(
        'DELETE',
        f'/api/knowledge/bases/{base["id"]}/documents/{current["id"]}',
        json={
            'expected_version': current['version'],
            'client_request_id': str(uuid4()),
        },
    )
    assert removed.status_code == 202, removed.text
    assert (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents/{current["id"]}/download')).status_code == 404
    async with container.database.sessions() as session:
        copy = await session.get(FileRecord, UUID(document['file_id']))
        source = await session.get(FileRecord, UUID(original['id']))
        assert copy.status == 'deleted' and copy.retention_until > utcnow()
        assert source.status == 'active'
        key = copy.storage_key
    assert await container.files.storage.read(key) == b'private project 8080'
    assert (await finish_job(admin, app, removed.json()))['status'] == 'completed'


async def test_repeated_multi_source_import_does_not_duplicate_materials_or_model_work(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base = await library(admin, provider)
    container = app.state.container
    container.knowledge_jobs.vectors = VectorFixture()
    sources = []
    for filename in ('one.txt', 'two.txt'):
        result = await admin.post(
            '/api/files',
            params={'filename': filename},
            content=b'content',
            headers={'Content-Type': 'application/octet-stream'},
        )
        sources.append(result.json()['id'])
    first = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={
            'file_ids': sources,
            'client_request_id': str(uuid4()),
        },
    )
    assert first.status_code == 202, first.text
    assert (await finish_job(admin, app, first.json()))['status'] == 'completed'
    previous_calls = len(provider.requests)
    repeat_body = {'file_ids': sources, 'client_request_id': str(uuid4())}
    repeated = await admin.post(f'/api/knowledge/bases/{base["id"]}/documents', json=repeat_body)
    assert repeated.status_code == 202 and repeated.json()['status'] == 'completed', repeated.text
    replay = await admin.post(f'/api/knowledge/bases/{base["id"]}/documents', json=repeat_body)
    assert replay.json()['id'] == repeated.json()['id']
    documents = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items']
    assert len(documents) == 2 and len({item['file_id'] for item in documents}) == 2
    assert len(provider.requests) == previous_calls


@pytest.mark.parametrize('revocation', ['library_acl', 'source_acl'])
async def test_migration_publish_rechecks_permissions_after_preflight_in_another_transaction(  # noqa: PLR0915 - coordinate real preflight, revocation and publish transactions
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    revocation: str,
) -> None:
    import asyncio

    from zhiheng.modules.files.public import AclRequest
    from zhiheng.modules.knowledge.public import BaseAcl
    from zhiheng.runtime.orm import Run
    from zhiheng.worker.execution import execute_run

    base = await library(admin, provider)
    container = app.state.container
    container.knowledge_jobs.vectors = VectorFixture()
    async with container.database.sessions.begin() as session:
        (await session.get(KnowledgeBase, UUID(base['id']))).document_policy = 'legacy'
    source = (
        await admin.post(
            '/api/files?filename=race.txt', content=b'old source', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()
    imported = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={
            'file_ids': [source['id']],
            'client_request_id': str(uuid4()),
        },
    )
    assert (await finish_job(admin, app, imported.json()))['status'] == 'completed'
    base = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
    queued = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/migration',
        json={
            'expected_version': base['version'],
            'expected_acl_version': base['acl_version'],
            'confirm_shared_access': True,
            'client_request_id': str(uuid4()),
        },
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()
    for _ in range(30):
        if job['phase'] == 'publish':
            break
        await container.knowledge_jobs.tick(UUID(job['id']))
        async with container.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(container, run_id)
        job = (await admin.get(f'/api/knowledge/jobs/{job["id"]}')).json()
    assert job['phase'] == 'publish', job
    async with container.database.sessions.begin() as session:
        claim = await container.knowledge_jobs.store.claim(session, UUID(job['id']))
    assert claim is not None
    prechecked, resume = asyncio.Event(), asyncio.Event()
    held_snapshots = []
    authorize = container.knowledge_jobs.authorize_origin

    async def stop_after_preflight(session: Any, current: Any, **kwargs: Any) -> Any:
        result = await authorize(session, current, **kwargs)
        # Retain both ORM objects to make stale identity-map state deterministic.
        held_snapshots.extend(
            [await session.get(KnowledgeBase, UUID(base['id'])), await session.get(FileRecord, UUID(source['id']))]
        )
        prechecked.set()
        await resume.wait()
        return result

    monkeypatch.setattr(container.knowledge_jobs, 'authorize_origin', stop_after_preflight)
    publisher = asyncio.create_task(container.knowledge_jobs._publish(claim))
    try:
        await asyncio.wait_for(prechecked.wait(), 3)
        async with container.database.sessions.begin() as revoking:
            owner = await container.identity.principal_for_user(revoking, UUID(base['owner_id']))
            if revocation == 'library_acl':
                await container.knowledge.set_acl(
                    revoking,
                    owner,
                    UUID(base['id']),
                    BaseAcl(
                        expected_version=base['acl_version'],
                        access='private',
                        entries=[],
                    ),
                )
            else:
                await container.files.set_acl(
                    revoking,
                    owner,
                    UUID(source['id']),
                    AclRequest(
                        expected_version=source['acl_version'],
                        access='private',
                        entries=[],
                    ),
                )
        resume.set()
        with pytest.raises(AppError) as rejected:
            await asyncio.wait_for(publisher, 3)
        assert rejected.value.code == 'knowledge_migration_stale'
    finally:
        resume.set()
        await asyncio.gather(publisher, return_exceptions=True)
    async with container.database.sessions() as session:
        retained = await session.get(KnowledgeBase, UUID(base['id']))
        assert retained.document_policy == 'legacy' and retained.active_revision == UUID(base['active_revision'])
        assert (
            await session.scalar(
                select(FileReference.id).where(
                    FileReference.file_id == UUID(source['id']),
                    FileReference.resource_type == 'knowledge_document',
                )
            )
            is not None
        )


@pytest.mark.parametrize('target_kind', ['source', 'artifact'])
async def test_locked_library_file_read_waits_for_base_before_locking_source_or_artifact(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    target_kind: str,
) -> None:
    import asyncio

    base = await library(admin, provider)
    source = await sent_source(admin, app)
    added = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/attachments',
        json={
            'file_id': source['id'],
            'client_request_id': str(uuid4()),
        },
    )
    document = added.json()['document']
    container = app.state.container
    source_id = UUID(document['file_id'])
    if target_kind == 'artifact':
        preview = await admin.get(f'/api/knowledge/bases/{base["id"]}/documents/{document["id"]}/preview')
        assert preview.status_code == 200, preview.text
        async with container.database.sessions() as session:
            target_id = await session.scalar(select(FileRecord.id).where(FileRecord.source_file_id == source_id))
    else:
        target_id = source_id
    assert target_id is not None
    reached_base, release_authorization = asyncio.Event(), asyncio.Event()
    authorize = container.files.library_access
    assert authorize is not None

    async def paused_library_access(session: Any, principal: Any, file_id: UUID, *, lock: bool = False) -> bool:
        if file_id == source_id:
            reached_base.set()
        allowed = await authorize(session, principal, file_id, lock=lock)
        if file_id == source_id:
            await release_authorization.wait()
        return allowed

    monkeypatch.setattr(container.files, 'library_access', paused_library_access)
    async with container.database.sessions() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))

    async def read_with_lock() -> None:
        async with container.database.sessions.begin() as session:
            await container.files.require(session, owner, target_id, active=True, lock=True)

    reading = None
    try:
        async with container.database.sessions.begin() as changing:
            await container.knowledge.authorize_base(changing, owner, UUID(base['id']), lock=True)
            reading = asyncio.create_task(read_with_lock())
            await asyncio.wait_for(reached_base.wait(), 3)
            # Correct KB -> file ordering leaves this source/artifact available to the KB transaction.
            await changing.execute(select(FileRecord.id).where(FileRecord.id == target_id).with_for_update(nowait=True))
        release_authorization.set()
        await asyncio.wait_for(reading, 3)
    finally:
        release_authorization.set()
        if reading is not None:
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)


@pytest.mark.parametrize('operation', ['attach', 'bind_sources'])
async def test_multi_library_file_admission_locks_all_bases_before_any_files(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    import asyncio
    import hashlib

    from zhiheng.modules.files.public import AttachmentSummary, PreparedAttachment, SourceSnapshot
    from zhiheng.modules.knowledge.orm import KnowledgeMaterial

    left = await library(admin, provider, 'left')
    right = await library(admin, provider, 'right')
    low_base, high_base = sorted([UUID(left['id']), UUID(right['id'])])
    low_file, high_file = sorted([uuid4(), uuid4()])
    container = app.state.container
    records = []
    async with container.database.sessions.begin() as session:
        owner = await container.identity.principal_for_user(session, UUID(left['owner_id']))
        for base_id, file_id in ((high_base, low_file), (low_base, high_file)):
            content = b'content'
            key = await container.files.storage.put(content)
            file = FileRecord(
                id=file_id,
                owner_id=owner.id,
                uploader_id=owner.id,
                storage_key=key,
                original_filename='source.txt',
                display_name='source.txt',
                size=len(content),
                mime='text/plain',
                sha256=hashlib.sha256(content).hexdigest(),
                purpose='knowledge_document',
                storage_status='normal',
            )
            session.add(file)
            await session.flush()
            document = KnowledgeDocument(
                base_id=base_id,
                file_id=file_id,
                file_name=file.original_filename,
                file_size=file.size,
                mime=file.mime,
                source_sha256=file.sha256,
                chunk_size=1000,
                chunk_overlap=200,
            )
            session.add(document)
            await session.flush()
            session.add(KnowledgeMaterial(base_id=base_id, file_id=file_id, document_id=document.id))
            records.append(file)
    attachments = tuple(
        PreparedAttachment(
            summary=AttachmentSummary(
                file_id=file.id,
                original_filename=file.original_filename,
                mime=file.mime,
                size=file.size,
                sha256=file.sha256,
            ),
            version=file.version,
            acl_version=file.acl_version,
            text='content',
        )
        for file in records
    )
    snapshots = tuple(
        SourceSnapshot(file_id=file.id, sha256=file.sha256, version=file.version, acl_version=file.acl_version)
        for file in records
    )
    reached_low = asyncio.Event()
    require = container.knowledge.require
    reading = None
    try:
        async with container.database.sessions.begin() as changing:
            await require(changing, owner, low_base, lock=True)

            async def observed_require(session: Any, principal: Any, base_id: UUID, **kwargs: Any) -> Any:
                if session is not changing and base_id == low_base:
                    reached_low.set()
                return await require(session, principal, base_id, **kwargs)

            monkeypatch.setattr(container.knowledge, 'require', observed_require)

            async def admit() -> None:
                async with container.database.sessions.begin() as session:
                    if operation == 'attach':
                        await container.files.attach(
                            session, owner, attachments, resource_type='chat', resource_id=uuid4()
                        )
                    else:
                        await container.files.bind_sources(
                            session, owner, snapshots, resource_type='knowledge_run', resource_id=uuid4()
                        )

            reading = asyncio.create_task(admit())
            await asyncio.wait_for(reached_low.wait(), 3)
            # Opposite file UUID order must never cause admission to hold the higher KB first.
            await changing.execute(
                select(KnowledgeBase.id).where(KnowledgeBase.id == high_base).with_for_update(nowait=True)
            )
        await asyncio.wait_for(reading, 3)
    finally:
        if reading is not None:
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)


async def test_console_legacy_import_orders_material_source_and_target_bases_before_files(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    from zhiheng.modules.knowledge.public import BuildSelection

    left = await library(admin, provider, 'left')
    right = await library(admin, provider, 'right')
    source_base, target_base = sorted([left, right], key=lambda base: UUID(base['id']))
    container = app.state.container
    async with container.database.sessions.begin() as session:
        (await session.get(KnowledgeBase, UUID(target_base['id']))).document_policy = 'legacy'
        owner = await container.identity.principal_for_user(session, UUID(target_base['owner_id']))
    attachment = await sent_source(admin, app)
    added = await admin.post(
        f'/api/knowledge/bases/{source_base["id"]}/attachments',
        json={
            'file_id': attachment['id'],
            'client_request_id': str(uuid4()),
        },
    )
    source_file_id = UUID(added.json()['document']['file_id'])
    reached_source = asyncio.Event()
    require = container.knowledge.require
    importing = None
    try:
        async with container.database.sessions.begin() as querying:
            await require(querying, owner, UUID(source_base['id']), lock=True)

            async def observed_require(session: Any, principal: Any, base_id: UUID, **kwargs: Any) -> Any:
                if session is not querying and base_id == UUID(source_base['id']) and kwargs.get('lock'):
                    reached_source.set()
                return await require(session, principal, base_id, **kwargs)

            monkeypatch.setattr(container.knowledge, 'require', observed_require)

            async def import_legacy() -> None:
                async with container.database.sessions.begin() as session:
                    await container.knowledge_commands.native.console_job(
                        session,
                        owner,
                        UUID(target_base['id']),
                        'import',
                        uuid4(),
                        {'file_ids': [str(source_file_id)]},
                        BuildSelection(file_ids=[source_file_id]),
                    )

            importing = asyncio.create_task(import_legacy())
            await asyncio.wait_for(reached_source.wait(), 3)
            await querying.execute(
                select(KnowledgeBase.id).where(KnowledgeBase.id == UUID(target_base['id'])).with_for_update(nowait=True)
            )
        with pytest.raises(AppError) as rejected:
            await asyncio.wait_for(importing, 3)
        assert rejected.value.code == 'knowledge_migration_required'
    finally:
        if importing is not None:
            importing.cancel()
            await asyncio.gather(importing, return_exceptions=True)


@pytest.mark.parametrize('transport', ['console', 'resource'])
@pytest.mark.parametrize('source_kind', ['managed_original', 'library_material'])
async def test_legacy_import_keeps_originals_but_requires_migration_for_library_materials(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    transport: str,
    source_kind: str,
) -> None:
    from tests.core.integration.test_resource_key_scopes import application, issue
    from zhiheng.modules.knowledge.orm import KnowledgeJob, KnowledgeMaterial

    target = await library(admin, provider, 'Legacy target')
    donor = await library(admin, provider, 'Library donor')
    container = app.state.container
    async with container.database.sessions.begin() as session:
        (await session.get(KnowledgeBase, UUID(target['id']))).document_policy = 'legacy'
    source = await sent_source(admin, app)
    added = await admin.post(
        f'/api/knowledge/bases/{donor["id"]}/attachments',
        json={
            'file_id': source['id'],
            'client_request_id': str(uuid4()),
        },
    )
    material_id = added.json()['document']['file_id']
    file_id = source['id'] if source_kind == 'managed_original' else material_id
    headers = {}
    if transport == 'resource':
        rate = await admin.post(
            f'/api/models/{target["embed_model_id"]}/rate-cards',
            json={'billing_mode': 'token', 'input_price': '1', 'output_price': '0'},
        )
        assert rate.status_code == 201, rate.text
        owner_app = await application(admin)
        key = await issue(
            admin,
            owner_app['id'],
            scopes=['knowledge:write', 'knowledge:read', 'files:read'],
            model_ids=[target['embed_model_id']],
        )
        for binding_index, (kind, identifier, actions) in enumerate(
            (
                ('knowledge_base', target['id'], ['read', 'write']),
                ('file', file_id, ['read']),
            )
        ):
            async with container.database.sessions.begin() as session:
                caller = await container.resource_access.bearer(session, key['api_key'], scope='knowledge:write')
                await container.resource_access.bind(
                    session,
                    caller.principal,
                    UUID(owner_app['id']),
                    kind,
                    UUID(identifier),
                    actions,
                    expected_version=1 + binding_index,
                )
        endpoint = f'/api/v1/knowledge-bases/{target["id"]}/documents'
        headers = {'Authorization': 'Bearer ' + key['api_key'], 'Idempotency-Key': str(uuid4())}
        body = {'file_ids': [file_id]}
    else:
        endpoint = f'/api/knowledge/bases/{target["id"]}/documents'
        body = {'file_ids': [file_id], 'client_request_id': str(uuid4())}
    async with container.database.sessions() as session:
        before = {
            model.__tablename__: await session.scalar(select(func.count()).select_from(model))
            for model in (FileRecord, KnowledgeDocument, KnowledgeJob, KnowledgeMaterial)
        }
    response = await admin.post(endpoint, json=body, headers=headers)
    if source_kind == 'library_material':
        assert response.status_code == 409 and response.json()['error']['code'] == 'knowledge_migration_required', (
            response.text
        )
        async with container.database.sessions() as session:
            after = {
                model.__tablename__: await session.scalar(select(func.count()).select_from(model))
                for model in (FileRecord, KnowledgeDocument, KnowledgeJob, KnowledgeMaterial)
            }
        assert after == before
    else:
        assert response.status_code == 202, response.text
        async with container.database.sessions() as session:
            documents = list(
                await session.scalars(select(KnowledgeDocument).where(KnowledgeDocument.base_id == UUID(target['id'])))
            )
            assert len(documents) == 1 and documents[0].file_id == UUID(file_id)
            assert (await session.get(KnowledgeBase, UUID(target['id']))).document_policy == 'legacy'
            assert await session.scalar(select(func.count()).select_from(FileRecord)) == before['files']
