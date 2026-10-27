from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update

from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_knowledge_failures import finish_job, ready_base, source
from tests.core.integration.test_work import member_client
from tests.core.provider import ProviderServer
from zhiheng.modules.knowledge.orm import IndexGeneration, KnowledgeDocument, KnowledgeJob, KnowledgeUpload
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


@pytest.mark.parametrize(
    ('staged_folder', 'target_folder', 'other_base'),
    [('A', 'B', False), (None, 'B', False), ('A', None, False), ('A', None, True)],
    ids=['folder-to-folder', 'root-to-folder', 'folder-to-root', 'cross-base'],
)
async def test_staged_upload_scope_rejection_preserves_transaction_and_replay(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    staged_folder: str | None,
    target_folder: str | None,
    other_base: bool,
) -> None:
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Scoped', 'embed_model_id': model})).json()
    endpoint = f'/api/knowledge/bases/{base["id"]}'
    folders = {name: (await admin.post(endpoint + '/folders', json={'name': name})).json()['id'] for name in ('A', 'B')}
    params = {'filename': 'staged.txt'}
    if staged_folder:
        params['folder_id'] = folders[staged_folder]
    uploaded = (
        await admin.post(
            endpoint + '/uploads',
            params=params,
            content=b'Staged source',
            headers={'content-type': 'application/octet-stream'},
        )
    ).json()
    file_id = uploaded['file']['id']
    ordinary = await source(admin, 'ordinary.txt', b'Ordinary managed source')
    references = (await admin.get(f'/api/files/{file_id}/references')).json()
    destination = endpoint
    if other_base:
        target = (await admin.post('/api/knowledge/bases', json={'name': 'Other', 'embed_model_id': model})).json()
        destination = f'/api/knowledge/bases/{target["id"]}'
    body = {
        'file_ids': [ordinary['id'], file_id],
        'folder_id': folders[target_folder] if target_folder else None,
        'client_request_id': str(uuid4()),
    }
    container = app.state.container
    rejected = await admin.post(destination + '/documents', json=body)
    assert rejected.status_code == 422 and rejected.json()['error']['code'] == 'invalid_source', rejected.text
    assert (await admin.get(f'/api/files/{file_id}/references')).json() == references
    async with container.database.sessions() as session:
        stage = await session.get(KnowledgeUpload, UUID(uploaded['upload_id']))
        assert stage is not None and stage.folder_id == (UUID(folders[staged_folder]) if staged_folder else None)
        for table in (KnowledgeDocument, KnowledgeJob, IndexGeneration, Run):
            assert await session.scalar(select(func.count()).select_from(table)) == 0
    assert (await admin.get('/api/usage')).json()['records'] == [] and provider.requests == []
    body['folder_id'] = folders[staged_folder] if staged_folder else None
    matching = await admin.post(endpoint + '/documents', json=body)
    assert matching.status_code == 202, matching.text
    async with container.database.sessions() as session:
        assert await session.get(KnowledgeUpload, UUID(uploaded['upload_id'])) is None
        assert await session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 2
    await admin.patch(f'/api/models/{model}', json={'enabled': False})
    replay = await admin.post(endpoint + '/documents', json=body)
    assert replay.status_code == 202 and replay.json()['id'] == matching.json()['id'], replay.text
    assert provider.requests == []


async def test_recursive_folder_delete_releases_staging_and_gc_respects_pins(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base = await ready_base(admin, app, provider)
    container = app.state.container
    endpoint = f'/api/knowledge/bases/{base["id"]}'
    root = (await admin.post(endpoint + '/folders', json={'name': 'Parent'})).json()
    child = (await admin.post(endpoint + '/folders', json={'name': 'Child', 'parent_id': root['id']})).json()
    uploaded = []
    for name in ('indexed.txt', 'staged.txt'):
        response = await admin.post(
            endpoint + '/uploads',
            params={'filename': name, 'folder_id': child['id']},
            content=b'Scoped knowledge.',
            headers={'content-type': 'application/octet-stream'},
        )
        assert response.status_code == 201, response.text
        uploaded.append(response.json())
    imported = await finish_job(
        admin,
        app,
        (
            await admin.post(
                endpoint + '/documents',
                json={
                    'file_ids': [uploaded[0]['file']['id']],
                    'folder_id': child['id'],
                    'client_request_id': str(uuid4()),
                },
            )
        ).json(),
    )
    assert imported['status'] == 'completed'
    pinned = (await admin.post(endpoint + '/query', json={'query': 'scoped', 'client_request_id': str(uuid4())})).json()
    deleted = await admin.request(
        'DELETE',
        endpoint + '/folders/' + root['id'],
        json={
            'expected_version': root['version'],
            'client_request_id': str(uuid4()),
        },
    )
    assert deleted.status_code == 202, deleted.text
    deleted = await finish_job(admin, app, deleted.json())
    assert deleted['status'] == 'completed', str(deleted)
    assert (await admin.get(endpoint + '/folders')).json() == []
    assert (await admin.get(endpoint + '/documents')).json()['total'] == 1
    for item in uploaded:
        info = (await admin.get('/api/files/' + item['file']['id'])).json()
        assert info['status'] == 'active' and info['reference_count'] == 0
    old, current = UUID(imported['index_revision']), UUID(deleted['index_revision'])
    async with container.database.sessions.begin() as session:
        await session.execute(
            update(IndexGeneration)
            .where(IndexGeneration.status == 'retired')
            .values(gc_after=utcnow() - timedelta(seconds=1))
        )
        assert old not in await container.knowledge_jobs.generations.pending(session)
    await admin.post(f'/api/knowledge/preparations/{pinned["id"]}/cancel', json={})
    async with container.database.sessions() as session:
        assert old in await container.knowledge_jobs.generations.pending(session)
    await container.knowledge_jobs.gc(old)
    assert old not in container.knowledge_jobs.vectors.rows
    assert current in container.knowledge_jobs.vectors.rows


async def test_cancelled_paid_step_is_zero_before_provider_and_stale_attempt_cannot_publish(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    base = await ready_base(admin, app, provider)
    container = app.state.container
    endpoint = f'/api/knowledge/bases/{base["id"]}'
    original = (await admin.get(endpoint)).json()['active_revision']
    file = await source(admin, 'new.txt', b'New knowledge')
    job = (
        await admin.post(endpoint + '/documents', json={'file_ids': [file['id']], 'client_request_id': str(uuid4())})
    ).json()
    for _ in range(5):
        await container.knowledge_jobs.tick(UUID(job['id']))
        job = (await admin.get('/api/knowledge/jobs/' + job['id'])).json()
        if job['status'] == 'waiting_model':
            break
    assert job['status'] == 'waiting_model'
    async with container.database.sessions() as session:
        child_id = await session.scalar(select(Run.id).where(Run.status == 'pending'))
    assert (await admin.post('/api/knowledge/jobs/' + job['id'] + '/cancel', json={})).json()[
        'status'
    ] == 'cancel_requested'
    await container.knowledge_jobs.tick(UUID(job['id']))
    child = (await admin.get(f'/api/runs/{child_id}')).json()
    assert (
        child['status'] == 'cancelled' and child['usage']['status'] == 'known' and child['usage']['input_tokens'] == 0
    )
    assert len(provider.requests) == 1
    assert (await admin.get(endpoint)).json()['active_revision'] == original
    next_job = (
        await admin.post(endpoint + '/documents', json={'file_ids': [file['id']], 'client_request_id': str(uuid4())})
    ).json()
    async with container.database.sessions.begin() as session:
        claim = await container.knowledge_jobs.store.claim(session, UUID(next_job['id']))
        await session.execute(
            update(KnowledgeJob).where(KnowledgeJob.id == claim.id).values(lease_until=utcnow() - timedelta(seconds=1))
        )
    async with container.database.sessions.begin() as session:
        assert await container.knowledge_jobs.store.claim(session, claim.id) is None
        with pytest.raises(AppError) as error:
            await container.knowledge_jobs.store.publish(session, claim, claim.binding)
        assert error.value.code == 'execution_lost'
    assert (await admin.get(endpoint)).json()['active_revision'] == original


async def test_admin_upload_preserves_original_owner_and_actual_uploader(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
) -> None:
    model = await embedding_model(admin, provider)
    actor_id = (await admin.get('/api/auth/me')).json()['id']
    async with member_client(app) as owner:
        owner_id = (await owner.get('/api/auth/me')).json()['id']
        base = (await owner.post('/api/knowledge/bases', json={'name': 'Owned', 'embed_model_id': model})).json()
        users = await owner.get(f'/api/knowledge/bases/{base["id"]}/users')
        assert users.status_code == 200 and all(set(user) == {'id', 'username'} for user in users.json())
        assert (await owner.get('/api/files/users')).status_code == 403
    uploaded = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/uploads',
        params={'filename': 'owned.txt'},
        content=b'Owned source',
        headers={'content-type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()['file']['owner_id'] == owner_id
    assert uploaded.json()['file']['uploader_id'] == actor_id
