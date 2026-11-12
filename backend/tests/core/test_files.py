import hashlib
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.provider import ProviderServer
from zhiheng.bootstrap.container import Container
from zhiheng.shared.principal import Principal
from zhiheng.shared.storage import StorageEntry


async def test_files_upload_permissions_range_cas_and_retention(admin: httpx.AsyncClient, app: FastAPI) -> None:
    uploaded = await admin.post(
        '/api/files?filename=hello.txt', content=b'hello world', headers={'Content-Type': 'application/octet-stream'}
    )
    assert uploaded.status_code == 201, uploaded.text
    value = uploaded.json()
    file_id = value['id']
    assert value['sha256'] == hashlib.sha256(b'hello world').hexdigest()
    ranged = await admin.get(f'/api/files/{file_id}/download', headers={'Range': 'bytes=1-4'})
    assert ranged.status_code == 206 and ranged.content == b'ello'
    assert ranged.headers['content-range'] == 'bytes 1-4/11'
    assert (await admin.get(f'/api/files/{file_id}/download', headers={'Range': 'bytes=99-'})).status_code == 416
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get(f'/api/files/{file_id}')).status_code == 404
        users = (await admin.get('/api/files/users')).json()
        member_id = next(u['id'] for u in users if u['username'] == 'member')
        acl = await admin.patch(
            f'/api/files/{file_id}/acl',
            json={'expected_version': 1, 'access': 'private', 'entries': [{'user_id': member_id, 'effect': 'allow'}]},
        )
        assert acl.status_code == 200, acl.text
        assert (await member.get(f'/api/files/{file_id}/download')).content == b'hello world'
        assert (
            await member.patch(f'/api/files/{file_id}', json={'expected_version': 1, 'display_name': 'stolen'})
        ).status_code == 404
        assert (
            await admin.patch(
                f'/api/files/{file_id}/acl', json={'expected_version': 1, 'access': 'public', 'entries': []}
            )
        ).status_code == 409
    current = (await admin.get(f'/api/files/{file_id}')).json()
    deleted = await admin.request('DELETE', f'/api/files/{file_id}', json={'expected_version': current['version']})
    assert deleted.status_code == 200, deleted.text
    assert (await admin.get(f'/api/files/{file_id}/download')).status_code == 404
    restored = await admin.post(f'/api/files/{file_id}/restore', json={'expected_version': deleted.json()['version']})
    assert restored.status_code == 200, restored.text


@pytest.mark.parametrize('filename', ['../evil.txt', 'a/b', 'a\\b', '.', '%00'])
async def test_unsafe_file_names(admin: httpx.AsyncClient, filename: str) -> None:
    result = await admin.post(
        '/api/files', params={'filename': filename}, content=b'x', headers={'Content-Type': 'application/octet-stream'}
    )
    assert result.status_code == 422, result.text


async def test_native_text_parse_and_chat_references(admin: httpx.AsyncClient, model_id: str) -> None:
    upload = await admin.post(
        '/api/files?filename=context.txt',
        content='原生附件正文'.encode(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert upload.status_code == 201, upload.text
    fid = upload.json()['id']
    parsed = await admin.post(f'/api/files/{fid}/parse', json={})
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()['text'] == '原生附件正文'
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    request_id = str(uuid4())
    body = {'model_id': model_id, 'message': 'read it', 'client_request_id': request_id, 'file_ids': [fid]}
    result = await admin.post(f'/api/chat/conversations/{conversation}/runs', json=body)
    assert result.status_code == 202, result.text
    messages = (await admin.get(f'/api/chat/conversations/{conversation}/messages')).json()
    assert messages[0]['attachments'][0]['file_id'] == fid
    refs = (await admin.get(f'/api/files/{fid}/references')).json()
    assert len(refs) >= 1
    assert (
        await admin.post(f'/api/chat/conversations/{conversation}/runs', json={**body, 'file_ids': []})
    ).status_code == 409


async def test_acl_deny_expiry_transfer_and_single_purge(admin: httpx.AsyncClient, app: FastAPI) -> None:
    fid = (
        await admin.post(
            '/api/files?filename=acl.txt', content=b'ACL', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()['id']
    user = next(u for u in (await admin.get('/api/files/users')).json() if u['username'] == 'member')
    acl = await admin.patch(
        f'/api/files/{fid}/acl',
        json={'expected_version': 1, 'access': 'public', 'entries': [{'user_id': user['id'], 'effect': 'deny'}]},
    )
    assert acl.status_code == 200, acl.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get(f'/api/files/{fid}/download')).status_code == 404
    current = (await admin.get(f'/api/files/{fid}')).json()
    transfer = await admin.post(
        f'/api/files/{fid}/transfer',
        json={'expected_version': current['version'], 'owner_id': user['id'], 'uploader_access_enabled': False},
    )
    assert transfer.status_code == 200, transfer.text
    denied = await admin.request('DELETE', f'/api/files/{fid}', json={'expected_version': transfer.json()['version']})
    assert denied.status_code == 404, denied.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        deleted = await member.request(
            'DELETE', f'/api/files/{fid}', json={'expected_version': transfer.json()['version']}
        )
        assert deleted.status_code == 200, deleted.text
        purged = await member.post(f'/api/files/{fid}/purge', json={'expected_version': deleted.json()['version']})
        assert purged.status_code == 200 and purged.json()['status'] == 'purged', purged.text
        assert (
            await member.post(f'/api/files/{fid}/restore', json={'expected_version': purged.json()['version']})
        ).status_code == 409


async def test_retention_policy_and_jobs(admin: httpx.AsyncClient) -> None:
    result = await admin.post(
        '/api/files/policies',
        json={'resource_type': 'chat', 'retention_days': 30, 'enabled': True, 'remark': 'history'},
    )
    assert result.status_code == 201, result.text
    assert (await admin.get('/api/files/policies')).json()[0]['resource_type'] == 'chat'
    result = await admin.post('/api/files/jobs/reconcile', json={})
    assert result.status_code == 200 and result.json()['status'] == 'succeeded', result.text
    assert (await admin.get('/api/files/reconcile/issues')).status_code == 200


async def test_real_image_wire_and_snapshot_replay(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    from io import BytesIO
    from uuid import UUID

    from PIL import Image

    from zhiheng.worker.execution import execute_run

    image = Image.new('RGB', (40, 30), 'blue')
    stream = BytesIO()
    image.save(stream, format='PNG')
    image.close()
    upload = await admin.post(
        '/api/files?filename=image.png', content=stream.getvalue(), headers={'Content-Type': 'application/octet-stream'}
    )
    fid = upload.json()['id']
    conversation = (await admin.post('/api/chat/conversations', json={})).json()['id']
    body = {'model_id': model_id, 'message': 'Describe image', 'client_request_id': str(uuid4()), 'file_ids': [fid]}
    rejected = await admin.post(f'/api/chat/conversations/{conversation}/runs', json=body)
    assert rejected.status_code == 422 and rejected.json()['error']['code'] == 'unsupported_capability', rejected.text
    assert (await admin.patch(f'/api/models/{model_id}', json={'vision_enabled': True})).status_code == 200
    result = await admin.post(f'/api/chat/conversations/{conversation}/runs', json=body)
    assert result.status_code == 202, result.text
    await execute_run(app.state.container, UUID(result.json()['id']))
    blocks = provider.requests[-1]['messages'][-1]['content']
    assert blocks[0]['text'] == 'Describe image'
    assert blocks[1]['type'] == 'image_url' and blocks[1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    from datetime import datetime, timedelta, timezone

    current = (await admin.get(f'/api/files/{fid}')).json()
    deleted = await admin.request('DELETE', f'/api/files/{fid}', json={'expected_version': current['version']})
    assert deleted.status_code == 409 and deleted.json()['error']['code'] == 'file_referenced', deleted.text
    expired = await admin.patch(
        f'/api/files/{fid}',
        json={
            'expected_version': current['version'],
            'expires_at': (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        },
    )
    assert expired.status_code == 200, expired.text
    assert (await admin.post(f'/api/chat/conversations/{conversation}/runs', json=body)).json()['id'] == result.json()[
        'id'
    ]
    fresh = await admin.post(
        f'/api/chat/conversations/{conversation}/runs', json={**body, 'file_ids': [], 'client_request_id': str(uuid4())}
    )
    assert fresh.status_code == 404, fresh.text
    purge = await admin.post(f'/api/files/{fid}/purge', json={'expected_version': expired.json()['version']})
    assert purge.status_code == 409, purge.text


async def test_derived_artifact_acl_expiry_and_gc(admin: httpx.AsyncClient, app: FastAPI) -> None:

    fid = (
        await admin.post(
            '/api/files?filename=source.txt', content=b'source', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()['id']
    await admin.patch(f'/api/files/{fid}/acl', json={'expected_version': 1, 'access': 'public', 'entries': []})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        parsed = await member.post(f'/api/files/{fid}/parse', json={})
        assert parsed.status_code == 200, parsed.text
        aid = parsed.json()['artifact_file_id']
        assert (await member.get(f'/api/files/{aid}/download')).status_code == 200
        await admin.patch(f'/api/files/{fid}/acl', json={'expected_version': 2, 'access': 'private', 'entries': []})
        assert (await member.get(f'/api/files/{aid}/download')).status_code == 404
    current = (await admin.get(f'/api/files/{fid}')).json()
    deleted = await admin.request('DELETE', f'/api/files/{fid}', json={'expected_version': current['version']})
    result = await admin.post(f'/api/files/{fid}/purge', json={'expected_version': deleted.json()['version']})
    assert result.status_code == 200, result.text
    assert (await admin.get(f'/api/files/{aid}')).status_code == 404


async def test_reconcile_missing_and_orphan_and_reminder(admin: httpx.AsyncClient, app: FastAPI) -> None:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from zhiheng.modules.files.orm import FileRecord

    upload = await admin.post(
        '/api/files?filename=missing.txt', content=b'missing', headers={'Content-Type': 'application/octet-stream'}
    )
    fid = upload.json()['id']
    container = app.state.container
    async with container.database.sessions() as session:
        row = await session.scalar(select(FileRecord).where(FileRecord.id == __import__('uuid').UUID(fid)))
        key = row.storage_key
    await container.files.storage.delete(key)
    result = await admin.post('/api/files/jobs/reconcile', json={})
    assert result.status_code == 200, result.text
    issues = (await admin.get('/api/files/reconcile/issues')).json()
    issue = next(i for i in issues if i['file_id'] == fid)
    assert issue['kind'] == 'missing'
    bad = await admin.post(
        f'/api/files/reconcile/issues/{issue["id"]}/actions', json={'expected_version': 1, 'action': 'repair'}
    )
    assert bad.status_code >= 400
    fid = (
        await admin.post(
            '/api/files?filename=soon.txt', content=b'soon', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()['id']
    await admin.patch(
        f'/api/files/{fid}',
        json={'expected_version': 1, 'expires_at': (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
    )
    for _ in range(2):
        result = await admin.post('/api/files/jobs/reminders', json={})
        assert result.status_code == 200, result.text
    reminders = (await admin.get('/api/files/reminders')).json()
    assert len(reminders) == 1
    read = await admin.post(f'/api/files/reminders/{reminders[0]["id"]}/read', json={})
    assert read.status_code == 200 and read.json()['is_read'] and read.json()['read_by'], read.text


async def test_purge_reservation_blocks_new_references_and_crash_reenters(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import UUID

    from zhiheng.shared.errors import AppError

    container = app.state.container
    upload = await admin.post(
        '/api/files?filename=crash.txt', content=b'crash', headers={'Content-Type': 'application/octet-stream'}
    )
    fid = upload.json()['id']
    prepared = await container.file_inputs.prepare((await _principal(container)), UUID(fid))
    current = (await admin.get(f'/api/files/{fid}')).json()
    deleted = await admin.request('DELETE', f'/api/files/{fid}', json={'expected_version': current['version']})
    original_delete = container.files.storage.delete

    async def interrupted(key: str) -> None:
        raise OSError('simulated crash before deletion')

    monkeypatch.setattr(container.files.storage, 'delete', interrupted)
    with pytest.raises(OSError):
        await container.file_jobs.purge(await _principal(container), UUID(fid), deleted.json()['version'])
    current = (await admin.get(f'/api/files/{fid}')).json()
    assert current['status'] == 'purging'
    async with container.database.sessions.begin() as session:
        with pytest.raises(AppError):
            await container.files.attach(
                session, await _principal(container), (prepared,), resource_type='chat', resource_id=uuid4()
            )
    monkeypatch.setattr(container.files.storage, 'delete', original_delete)
    result = await admin.post(f'/api/files/{fid}/purge', json={'expected_version': current['version']})
    assert result.status_code == 200 and result.json()['status'] == 'purged', result.text


async def _principal(container: Container) -> Principal:
    users = None
    async with container.database.sessions() as session:
        users = await container.identity.directory(session, search='admin')
        return await container.identity.principal_for_user(session, users[0].id)


async def test_manual_periodic_mutex_and_cancelled_job_fact(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from zhiheng.shared.errors import AppError

    container = app.state.container
    principal = await _principal(container)
    started = asyncio.Event()
    release = asyncio.Event()
    original = container.files.storage.entries

    async def slow_entries(*, after: str = '', limit: int = 100) -> tuple[StorageEntry, ...]:
        started.set()
        await release.wait()
        return await original(after=after, limit=limit)

    monkeypatch.setattr(container.files.storage, 'entries', slow_entries)
    task = asyncio.create_task(container.file_jobs.run(principal, 'reconcile'))
    await asyncio.wait_for(started.wait(), 2)
    blocked = await admin.post(
        f'/api/files/reconcile/issues/{uuid4()}/actions', json={'expected_version': 1, 'action': 'ignore'}
    )
    assert blocked.status_code == 409, blocked.text
    with pytest.raises(AppError) as error:
        await container.file_jobs.run(principal, 'expiry')
    assert error.value.code == 'file_job_busy'
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    jobs = (await admin.get('/api/files/jobs')).json()
    assert jobs[0]['status'] == 'failed' and jobs[0]['errors'] == ['cancelled']
    monkeypatch.setattr(container.files.storage, 'entries', original)
    assert (await admin.post('/api/files/jobs/expiry', json={})).status_code == 200


async def test_unknown_orphan_cannot_register_or_repair_and_quarantined_metadata_is_counted(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    import os

    container = app.state.container
    key = await container.files.storage.put(b'recovered text')
    path = container.settings.files_root / key
    os.utime(path, (1, 1))
    await admin.post('/api/files/jobs/reconcile', json={})
    issue = next(i for i in (await admin.get('/api/files/reconcile/issues')).json() if i['kind'] == 'orphan')
    result = await admin.post(
        f'/api/files/reconcile/issues/{issue["id"]}/actions',
        json={
            'expected_version': 1,
            'action': 'register',
            'original_filename': 'recovered.txt',
            'reason': 'operator recovery',
        },
    )
    assert result.status_code == 409 and result.json()['error']['code'] == 'orphan_owner_unconfirmed'
    quarantined = await admin.post(
        f'/api/files/reconcile/issues/{issue["id"]}/actions',
        json={
            'expected_version': 1,
            'action': 'quarantine',
            'original_filename': 'recovered.txt',
            'reason': 'owner unknown',
        },
    )
    assert quarantined.status_code == 200, quarantined.text
    fid = quarantined.json()['file_id']
    assert (await admin.post(f'/api/files/{fid}/parse', json={})).status_code == 404
    repair = await admin.post(
        f'/api/files/reconcile/issues/{issue["id"]}/actions',
        json={'expected_version': quarantined.json()['version'], 'action': 'repair', 'reason': 'no owner'},
    )
    assert repair.status_code == 409 and repair.json()['error']['code'] == 'orphan_owner_unconfirmed'
    content = path.read_bytes()
    path.unlink()
    assert (await admin.post('/api/files/jobs/reconcile', json={})).status_code == 200
    missing = next(i for i in (await admin.get('/api/files/reconcile/issues')).json() if i['file_id'] == fid)
    assert missing['kind'] == 'missing'
    path.write_bytes(content)
    repair = await admin.post(
        f'/api/files/reconcile/issues/{missing["id"]}/actions',
        json={'expected_version': missing['version'], 'action': 'repair'},
    )
    assert repair.status_code == 409 and repair.json()['error']['code'] == 'orphan_owner_unconfirmed'
    assert (await admin.get(f'/api/files/{fid}/download')).status_code == 404
    query = {
        'status': 'all',
        'search': 'recovered',
        'owner_search': 'admin',
        'access': 'private',
        'expiration_status': 'permanent',
    }
    listed = (await admin.get('/api/files', params=query)).json()
    stats = (await admin.get('/api/files/stats', params=query)).json()
    assert listed['total'] == stats['total_count'] == 1


async def test_chat_lifecycle_cas_busy_delete_releases_refs(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    from uuid import UUID

    from zhiheng.worker.execution import execute_run

    info = (await admin.post('/api/chat/conversations', json={})).json()
    cid = info['id']
    renamed = await admin.patch(
        f'/api/chat/conversations/{cid}', json={'expected_version': 1, 'title': 'Renamed', 'archived': True}
    )
    assert renamed.status_code == 200 and renamed.json()['archived'], renamed.text
    assert (await admin.get('/api/chat/conversations', params={'archived': True})).json()[0]['title'] == 'Renamed'
    assert (
        await admin.patch(f'/api/chat/conversations/{cid}', json={'expected_version': 1, 'title': 'stale'})
    ).status_code == 409
    fid = (
        await admin.post(
            '/api/files?filename=history.txt', content=b'history', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()['id']
    body = {'model_id': model_id, 'message': 'history', 'client_request_id': str(uuid4()), 'file_ids': [fid]}
    archived = await admin.post(f'/api/chat/conversations/{cid}/runs', json=body)
    assert archived.status_code == 409 and archived.json()['error']['code'] == 'conversation_archived', archived.text
    assert (await admin.get(f'/api/files/{fid}/references')).json() == []
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] == 0
    restored = await admin.patch(f'/api/chat/conversations/{cid}', json={'expected_version': 2, 'archived': False})
    assert restored.status_code == 200 and not restored.json()['archived'], restored.text
    version = restored.json()['version']
    assert version == 3
    run = await admin.post(
        f'/api/chat/conversations/{cid}/runs',
        json=body,
    )
    assert run.status_code == 202, run.text
    busy = await admin.request('DELETE', f'/api/chat/conversations/{cid}', json={'expected_version': version})
    assert busy.status_code == 409 and busy.json()['error']['code'] == 'conversation_busy', busy.text
    await execute_run(app.state.container, UUID(run.json()['id']))
    assert len((await admin.get(f'/api/files/{fid}/references')).json()) == 1
    result = await admin.request('DELETE', f'/api/chat/conversations/{cid}', json={'expected_version': version})
    assert result.status_code == 204, result.text
    assert (await admin.get(f'/api/files/{fid}/references')).json() == []
    assert (await admin.get(f'/api/chat/conversations/{cid}/messages')).status_code == 404


async def test_reminder_batches_progress_past_already_reminded_files(admin: httpx.AsyncClient, app: FastAPI) -> None:
    from datetime import datetime, timedelta, timezone

    from zhiheng.modules.files.orm import FileRecord

    container = app.state.container
    principal = await _principal(container)
    rows = []
    for index in range(101):
        key = await container.files.storage.put(b'bounded reminder')
        rows.append(
            FileRecord(
                storage_key=key,
                original_filename=f'{index}.txt',
                display_name=f'{index}.txt',
                size=16,
                mime='text/plain',
                sha256=hashlib.sha256(b'bounded reminder').hexdigest(),
                owner_id=principal.id,
                uploader_id=principal.id,
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
    async with container.database.sessions.begin() as session:
        session.add_all(rows)
    first = await admin.post('/api/files/jobs/reminders', json={})
    second = await admin.post('/api/files/jobs/reminders', json={})
    assert first.status_code == second.status_code == 200
    assert first.json()['changed'] + second.json()['changed'] == 101


async def test_download_access_audit_allowed_range_and_denied(admin: httpx.AsyncClient, app: FastAPI) -> None:
    fid = (
        await admin.post(
            '/api/files?filename=audit.txt', content=b'audit', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()['id']
    assert (await admin.get(f'/api/files/{fid}/download', headers={'Range': 'bytes=1-2'})).content == b'ud'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        assert (await member.get(f'/api/files/{fid}/download')).status_code == 404
        assert (await member.get(f'/api/files/{fid}/audit')).status_code == 404
    records = (await admin.get(f'/api/files/{fid}/audit')).json()
    downloads = [r for r in records if r['action'] == 'download']
    assert {r['details']['outcome'] for r in downloads} == {'allowed', 'denied'}
    assert next(r for r in downloads if r['details']['outcome'] == 'allowed')['details']['bytes_sent'] == 2
    page = await admin.get(f'/api/files/{fid}/audit/page', params={'outcome': 'denied', 'page_size': 1})
    assert page.status_code == 200 and page.json()['total'] == 1, page.text


async def test_derived_permission_tracks_owner_transfer_without_uploader_backdoor(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    container = app.state.container
    async with container.database.sessions.begin() as session:
        target = await container.identity.create_user(session, username='another', password='platform-test-password')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        fid = (
            await member.post(
                '/api/files?filename=owned.txt', content=b'owned', headers={'Content-Type': 'application/octet-stream'}
            )
        ).json()['id']
        artifact = (await member.post(f'/api/files/{fid}/parse', json={})).json()['artifact_file_id']
        transferred = await member.post(
            f'/api/files/{fid}/transfer',
            json={
                'expected_version': 1,
                'owner_id': str(target.id),
                'uploader_access_enabled': False,
                'reason': 'handover',
            },
        )
        assert transferred.status_code == 200, transferred.text
        assert (await member.get(f'/api/files/{artifact}/download')).status_code == 404
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as another:
            await another.post('/api/auth/login', json={'username': 'another', 'password': 'platform-test-password'})
            assert (await another.get(f'/api/files/{artifact}/download')).status_code == 200
            assert (
                await another.patch(
                    f'/api/files/{artifact}/acl', json={'expected_version': 1, 'access': 'public', 'entries': []}
                )
            ).status_code == 409


async def test_chat_accepts_more_than_fifteen_documents_within_total_size(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    ids = []
    for index in range(16):
        result = await admin.post(
            '/api/files',
            params={'filename': f'{index}.txt'},
            content=b'doc',
            headers={'Content-Type': 'application/octet-stream'},
        )
        ids.append(result.json()['id'])
    cid = (await admin.post('/api/chat/conversations', json={})).json()['id']
    result = await admin.post(
        f'/api/chat/conversations/{cid}/runs',
        json={'model_id': model_id, 'message': 'files', 'client_request_id': str(uuid4()), 'file_ids': ids},
    )
    assert result.status_code == 202, result.text
    messages = (await admin.get(f'/api/chat/conversations/{cid}/messages')).json()
    question = next(message for message in messages if message['role'] == 'user')
    assert [item['file_id'] for item in question['attachments']] == ids


@pytest.mark.parametrize('method', ['PATCH', 'DELETE'])
async def test_policy_invalid_path_is_http_validation(admin: httpx.AsyncClient, method: str) -> None:
    body = {'expected_version': 1}
    if method == 'PATCH':
        body.update(retention_days=30, enabled=True, remark='invalid path')
    response = await admin.request(method, '/api/files/policies/UPPER-INVALID', json=body)
    assert response.status_code in {404, 422}, response.text


async def test_chat_attachment_total_size_is_checked_before_parsing(admin: httpx.AsyncClient, model_id: str) -> None:
    ids = []
    for index in range(3):
        uploaded = await admin.post(
            '/api/chat/attachments',
            params={'filename': f'large-{index}.txt'},
            content=b'x' * (20 * 1024 * 1024),
            headers={'Content-Type': 'application/octet-stream'},
        )
        assert uploaded.status_code == 201, uploaded.text
        ids.append(uploaded.json()['id'])
    cid = (await admin.post('/api/chat/conversations', json={})).json()['id']
    response = await admin.post(
        f'/api/chat/conversations/{cid}/runs',
        json={'model_id': model_id, 'message': 'files', 'client_request_id': str(uuid4()), 'file_ids': ids},
    )
    assert response.status_code == 422, response.text
    assert response.json()['error']['code'] == 'attachments_too_large'
    assert (await admin.get(f'/api/chat/conversations/{cid}/messages')).json() == []
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] == 0


async def test_quarantine_survives_missing_then_healthy_reconcile(admin: httpx.AsyncClient, app: FastAPI) -> None:
    from uuid import UUID

    from zhiheng.modules.files.orm import FileRecord

    container = app.state.container
    fid = (
        await admin.post(
            '/api/files?filename=quarantine.txt',
            content=b'original',
            headers={'Content-Type': 'application/octet-stream'},
        )
    ).json()['id']
    async with container.database.sessions() as session:
        row = await session.get(FileRecord, UUID(fid))
        key = row.storage_key
    path = container.settings.files_root / key
    path.write_bytes(b'damaged')
    await admin.post('/api/files/jobs/reconcile', json={})
    issue = next(i for i in (await admin.get('/api/files/reconcile/issues')).json() if i['file_id'] == fid)
    quarantined = await admin.post(
        f'/api/files/reconcile/issues/{issue["id"]}/actions',
        json={'expected_version': 1, 'action': 'quarantine', 'reason': 'explicit operator isolation'},
    )
    assert quarantined.status_code == 200, quarantined.text
    path.unlink()
    await admin.post('/api/files/jobs/reconcile', json={})
    path.write_bytes(b'original')
    await admin.post('/api/files/jobs/reconcile', json={})
    assert (await admin.get(f'/api/files/{fid}')).json()['storage_status'] == 'quarantined'
    assert (await admin.get(f'/api/files/{fid}/download')).status_code == 404
    repaired = await admin.post(
        f'/api/files/reconcile/issues/{issue["id"]}/actions',
        json={
            'expected_version': quarantined.json()['version'],
            'action': 'repair',
            'reason': 'verified operator release',
        },
    )
    assert repaired.status_code == 200, repaired.text
    assert (await admin.get(f'/api/files/{fid}/download')).content == b'original'


async def test_file_name_queries_keep_all_matching_identities_beyond_picker_limit(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    from uuid import UUID, uuid4

    from zhiheng.modules.files.orm import FileGrant, FileRecord
    from zhiheng.modules.identity.orm import IdentityUser

    container = app.state.container
    admin_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    users = [
        IdentityUser(
            id=uuid4(), username=f'bulkowner_{i:03}', password_hash='not-used-for-login', is_active=True, is_admin=False
        )
        for i in range(101)
    ]
    async with container.database.sessions.begin() as session:
        session.add_all(users)
    files = []
    for index, user in enumerate(users):
        key = await container.files.storage.put(b'names')
        files.append(
            FileRecord(
                storage_key=key,
                original_filename=f'document-{index}.txt',
                display_name=f'document-{index}.txt',
                size=5,
                mime='text/plain',
                sha256=hashlib.sha256(b'names').hexdigest(),
                owner_id=user.id,
                uploader_id=user.id,
                storage_status='normal',
            )
        )
    async with container.database.sessions.begin() as session:
        session.add_all(files)
        await session.flush()
        session.add_all(FileGrant(file_id=file.id, user_id=admin_id, effect='allow') for file in files)
    for field in ('owner_search', 'uploader_search', 'search'):
        query = {'status': 'all', field: 'bulkowner_', 'page_size': 20}
        listed = (await admin.get('/api/files', params=query)).json()
        stats = (await admin.get('/api/files/stats', params=query)).json()
        assert listed['total'] == stats['total_count'] == 101, (field, listed['total'], stats['total_count'])
        assert len(listed['items']) == 20
    assert len((await admin.get('/api/files/users', params={'search': 'bulkowner_'})).json()) == 100


async def test_derived_artifact_maintenance_recovers_verified_bytes_without_public_management(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    from uuid import UUID

    from zhiheng.modules.files.orm import FileRecord

    container = app.state.container
    fid = (
        await admin.post(
            '/api/files?filename=recover.txt', content=b'recover', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()['id']
    artifact = (await admin.post(f'/api/files/{fid}/parse', json={})).json()['artifact_file_id']
    async with container.database.sessions() as session:
        row = await session.get(FileRecord, UUID(artifact))
        path = container.settings.files_root / row.storage_key
        backup = path.read_bytes()
    path.unlink()
    await admin.post('/api/files/jobs/reconcile', json={})
    issue = next(i for i in (await admin.get('/api/files/reconcile/issues')).json() if i['file_id'] == artifact)
    path.write_bytes(backup)
    repaired = await admin.post(
        f'/api/files/reconcile/issues/{issue["id"]}/actions',
        json={'expected_version': issue['version'], 'action': 'repair', 'reason': 'verified restored artifact'},
    )
    assert repaired.status_code == 200, repaired.text
    assert (await admin.post(f'/api/files/{fid}/parse', json={})).status_code == 200
    for method, suffix, body in [
        ('PATCH', '', {'expected_version': 2, 'display_name': 'changed'}),
        ('PATCH', '/acl', {'expected_version': 2, 'access': 'public', 'entries': []}),
        ('POST', '/transfer', {'expected_version': 2, 'owner_id': str((await _principal(container)).id)}),
        ('DELETE', '', {'expected_version': 2}),
        ('POST', '/restore', {'expected_version': 2}),
        ('POST', '/purge', {'expected_version': 2}),
    ]:
        response = await admin.request(method, f'/api/files/{artifact}{suffix}', json=body)
        assert response.status_code == 409 and response.json()['error']['code'] == 'derived_file_immutable', (
            response.text
        )


async def test_historical_and_new_images_share_admitted_context_budget(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    import random
    from io import BytesIO
    from uuid import UUID

    from PIL import Image
    from sqlalchemy import text

    from zhiheng.worker.execution import execute_run

    image = Image.frombytes('RGB', (1400, 1400), random.Random(47).randbytes(1400 * 1400 * 3))
    stream = BytesIO()
    image.save(stream, format='PNG')
    image.close()
    ids = [
        (
            await admin.post(
                '/api/files',
                params={'filename': f'noise-{index}.png'},
                content=stream.getvalue(),
                headers={'Content-Type': 'application/octet-stream'},
            )
        ).json()['id']
        for index in range(8)
    ]
    await admin.patch(f'/api/models/{model_id}', json={'vision_enabled': True})
    cid = (await admin.post('/api/chat/conversations', json={})).json()['id']
    body = {
        'model_id': model_id,
        'message': 'Describe managed images',
        'file_ids': ids,
        'client_request_id': str(uuid4()),
    }
    container = app.state.container
    tables = ('runtime_runs', 'chat_messages', 'file_references', 'developer_invocations', 'runtime_outbox')
    container.chat_context.input_tokens = 8192
    async with container.database.sessions() as session:
        empty = {table: await session.scalar(text(f'SELECT count(*) FROM {table}')) for table in tables}
    default_budget = await admin.post(f'/api/chat/conversations/{cid}/runs', json=body)
    assert default_budget.status_code == 422 and default_budget.json()['error']['code'] == 'context_input_too_long', (
        default_budget.text
    )
    assert (await admin.get('/api/usage')).json()['reserved_tokens'] == 0
    async with container.database.sessions() as session:
        assert empty == {table: await session.scalar(text(f'SELECT count(*) FROM {table}')) for table in tables}

    # A configured larger token budget lets the independent 16 MiB image cap be exercised.
    container.chat_context.input_tokens = 65536
    first = await admin.post(f'/api/chat/conversations/{cid}/runs', json=body)
    assert first.status_code == 202, first.text
    await execute_run(container, UUID(first.json()['id']))
    async with container.database.sessions() as session:
        before = {table: await session.scalar(text(f'SELECT count(*) FROM {table}')) for table in tables}
    usage_before = (await admin.get('/api/usage')).json()
    rejected = await admin.post(f'/api/chat/conversations/{cid}/runs', json={**body, 'client_request_id': str(uuid4())})
    assert rejected.status_code == 422 and rejected.json()['error']['code'] == 'attachment_images_too_large', (
        rejected.text
    )
    replay = await admin.post(f'/api/chat/conversations/{cid}/runs', json=body)
    assert replay.status_code == 202 and replay.json()['id'] == first.json()['id'], replay.text
    async with container.database.sessions() as session:
        after = {table: await session.scalar(text(f'SELECT count(*) FROM {table}')) for table in tables}
    assert before == after
    assert (await admin.get('/api/usage')).json() == usage_before


async def test_lost_job_connection_fences_stale_scan_and_success_cursor(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from uuid import UUID

    from sqlalchemy import select, text

    from zhiheng.modules.files.orm import FileJob, FileRecord

    container = app.state.container
    fid = (
        await admin.post(
            '/api/files?filename=guard.txt', content=b'original', headers={'Content-Type': 'application/octet-stream'}
        )
    ).json()['id']
    async with container.database.sessions() as session:
        row = await session.get(FileRecord, UUID(fid))
        key = row.storage_key
    path = container.settings.files_root / key
    path.write_bytes(b'bad')
    await admin.post('/api/files/jobs/reconcile', json={})
    issue = next(i for i in (await admin.get('/api/files/reconcile/issues')).json() if i['file_id'] == fid)
    path.write_bytes(b'original')
    started = asyncio.Event()
    release = asyncio.Event()
    original_read = container.files.storage.read

    async def paused(storage_key: str) -> bytes:
        if storage_key == key:
            started.set()
            await release.wait()
        return await original_read(storage_key)

    monkeypatch.setattr(container.files.storage, 'read', paused)
    task = asyncio.create_task(container.file_jobs.run(await _principal(container), 'reconcile'))
    try:
        await asyncio.wait_for(started.wait(), 3)
        async with container.database.sessions.begin() as session:
            pid = await session.scalar(
                text(
                    "SELECT l.pid FROM pg_locks l JOIN pg_stat_activity a ON a.pid=l.pid WHERE l.locktype='advisory' AND l.objid=784246993 AND l.granted AND a.datname=current_database()"
                )
            )
            assert pid is not None
            job = await session.scalar(select(FileJob).where(FileJob.status == 'running'))
            job_id = job.id
            assert await session.scalar(text('SELECT pg_terminate_backend(:pid)'), {'pid': pid})
        response = await admin.post(
            f'/api/files/reconcile/issues/{issue["id"]}/actions',
            json={'expected_version': issue['version'], 'action': 'quarantine', 'reason': 'isolate after guard loss'},
        )
        assert response.status_code == 200, response.text
    finally:
        release.set()
        try:
            await asyncio.wait_for(task, 5)
        except Exception:
            pass
    assert (await admin.get(f'/api/files/{fid}')).json()['storage_status'] == 'quarantined'
    async with container.database.sessions() as session:
        job = await session.get(FileJob, job_id)
        assert job.status == 'failed' and job.cursor == ''


@pytest.mark.parametrize(
    'derived,expired,count', [(False, False, 1), (False, True, 101), (True, False, 101), (True, True, 1)]
)
async def test_manual_delete_conservatively_blocks_entire_family_refs(
    admin: httpx.AsyncClient, app: FastAPI, derived: bool, expired: bool, count: int
) -> None:
    from datetime import datetime, timedelta, timezone
    from uuid import UUID

    from zhiheng.modules.files.orm import FileReference

    source = (
        await admin.post(
            '/api/files?filename=protected.txt',
            content=b'protected',
            headers={'Content-Type': 'application/octet-stream'},
        )
    ).json()
    parsed = (await admin.post(f'/api/files/{source["id"]}/parse', json={})).json()
    target = UUID(parsed['artifact_file_id'] if derived else source['id'])
    async with app.state.container.database.sessions.begin() as session:
        session.add_all(
            FileReference(
                file_id=target,
                resource_type='chat',
                resource_id=uuid4(),
                retain_until=datetime.now(timezone.utc) - timedelta(days=1) if expired else None,
            )
            for _ in range(count)
        )
    before = (await admin.get(f'/api/files/{source["id"]}')).json()
    audit = (await admin.get(f'/api/files/{source["id"]}/audit/page')).json()
    deleted = await admin.request('DELETE', f'/api/files/{source["id"]}', json={'expected_version': before['version']})
    assert deleted.status_code == 409 and deleted.json()['error']['code'] == 'file_referenced', deleted.text
    assert (await admin.get(f'/api/files/{source["id"]}')).json() == before
    assert (await admin.get(f'/api/files/{source["id"]}/audit/page')).json() == audit


async def test_duplicate_delete_keeps_retention_and_expiry_dispose_after_sweep(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    from datetime import datetime, timedelta, timezone
    from uuid import UUID

    from sqlalchemy import func, select

    from zhiheng.modules.files.orm import FileReference

    source = (
        await admin.post(
            '/api/files?filename=expiry-dispose.txt',
            content=b'expiry',
            headers={'Content-Type': 'application/octet-stream'},
        )
    ).json()
    parsed = (await admin.post(f'/api/files/{source["id"]}/parse', json={})).json()
    ids = [UUID(source['id']), UUID(parsed['artifact_file_id'])]
    async with app.state.container.database.sessions.begin() as session:
        session.add_all(
            FileReference(
                file_id=file_id,
                resource_type='chat',
                resource_id=uuid4(),
                retain_until=datetime.now(timezone.utc) - timedelta(days=1),
            )
            for file_id in ids
            for _ in range(51)
        )
    patched = await admin.patch(
        f'/api/files/{source["id"]}',
        json={'expected_version': 1, 'expires_at': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
    )
    assert patched.status_code == 200
    assert (await admin.post('/api/files/jobs/expiry', json={})).status_code == 200
    deleted = (await admin.get(f'/api/files/{source["id"]}')).json()
    assert deleted['status'] == 'deleted'
    duplicate = await admin.request(
        'DELETE', f'/api/files/{source["id"]}', json={'expected_version': deleted['version']}
    )
    assert duplicate.status_code == 409, duplicate.text
    assert (await admin.get(f'/api/files/{source["id"]}')).json() == deleted
    disposed = await admin.post(
        f'/api/files/{source["id"]}/dispose-expired', json={'expected_version': deleted['version']}
    )
    assert disposed.status_code == 200, disposed.text
    value = disposed.json()
    assert value['status'] == 'deleted' and value['version'] > deleted['version']
    assert value['deleted_at'] == deleted['deleted_at'] and value['retention_until'] == deleted['retention_until']
    async with app.state.container.database.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(FileReference).where(FileReference.file_id.in_(ids)))
            == 0
        )
    assert (await admin.post(f'/api/files/{source["id"]}/purge', json={'expected_version': value['version']})).json()[
        'status'
    ] == 'purged'


async def test_reminder_kind_is_current_and_obsolete_expiry_keys_are_hidden(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime, timedelta, timezone
    from uuid import UUID

    from sqlalchemy import select

    from zhiheng.modules.files import service as files_service
    from zhiheng.modules.files.orm import FileReminder

    now = datetime.now(timezone.utc)
    ids = []
    for filename, expiry in [('yesterday.txt', now - timedelta(days=1)), ('tomorrow.txt', now + timedelta(days=1))]:
        uploaded = (
            await admin.post(
                '/api/files',
                params={'filename': filename},
                content=b'expiry',
                headers={'Content-Type': 'application/octet-stream'},
            )
        ).json()
        ids.append(uploaded['id'])
        assert (
            await admin.patch(
                f'/api/files/{uploaded["id"]}', json={'expected_version': 1, 'expires_at': expiry.isoformat()}
            )
        ).status_code == 200
    await admin.post('/api/files/jobs/reminders', json={})
    reminders = (await admin.get('/api/files/reminders')).json()
    assert {r['original_filename']: r['kind'] for r in reminders} == {
        'yesterday.txt': 'expired',
        'tomorrow.txt': 'expiring',
    }
    future = next(r for r in reminders if r['file_id'] == ids[1])
    read = await admin.post(f'/api/files/reminders/{future["id"]}/read', json={})
    assert read.status_code == 200 and read.json()['kind'] == 'expiring', read.text
    with monkeypatch.context() as clock:
        clock.setattr(files_service, 'utcnow', lambda: now + timedelta(days=2))
        assert all(r['kind'] == 'expired' for r in (await admin.get('/api/files/reminders')).json())
        assert (await admin.post(f'/api/files/reminders/{future["id"]}/read', json={})).json()['kind'] == 'expired'
    assert (
        await admin.patch(f'/api/files/{ids[1]}', json={'expected_version': 2, 'expires_at': None})
    ).status_code == 200
    assert all(r['file_id'] != ids[1] for r in (await admin.get('/api/files/reminders')).json())
    obsolete = await admin.post(f'/api/files/reminders/{future["id"]}/read', json={})
    assert obsolete.status_code == 404, obsolete.text
    assert (
        await admin.patch(
            f'/api/files/{ids[1]}', json={'expected_version': 3, 'expires_at': (now + timedelta(days=2)).isoformat()}
        )
    ).status_code == 200
    await admin.post('/api/files/jobs/reminders', json={})
    current = [r for r in (await admin.get('/api/files/reminders')).json() if r['file_id'] == ids[1]]
    assert len(current) == 1 and current[0]['id'] != future['id'] and current[0]['kind'] == 'expiring'
    async with app.state.container.database.sessions() as session:
        rows = list(await session.scalars(select(FileReminder).where(FileReminder.file_id == UUID(ids[1]))))
        assert len(rows) == 2 and all(r.kind == 'expiry' for r in rows)
