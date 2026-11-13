"""The platform file protocol uses the native file lifecycle and current App ACLs."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.integration.test_resource_requests import setup_access, upload
from zhiheng.modules.billing.orm import Invocation
from zhiheng.modules.developer.orm import APIRequest, KeyMinuteBucket
from zhiheng.modules.files.orm import FileRecord
from zhiheng.shared.time import utcnow


def auth(key: dict[str, Any], external: UUID | None = None) -> dict[str, str]:
    headers = {'Authorization': 'Bearer ' + key['api_key']}
    if external is not None:
        headers['Idempotency-Key'] = str(external)
    return headers


async def post(
    client: httpx.AsyncClient, key: dict[str, Any], external: UUID | None = None, **kwargs: Any
) -> httpx.Response:
    return await client.post(
        '/api/v1/files',
        headers=auth(key, external or uuid4()),
        files=kwargs.pop('files', {'file': ('source.txt', b'four', 'text/plain')}),
        data=kwargs.pop('data', {'purpose': 'knowledge'}),
        **kwargs,
    )


def envelope(response: httpx.Response, status: int) -> None:
    assert response.status_code == status, response.text
    request_id = response.headers['X-Request-ID']
    UUID(request_id)
    assert response.json()['error']['request_id'] == request_id
    assert set(response.json()['error']) == {'code', 'message', 'details', 'request_id'}


async def test_resource_file_roundtrip_range_etag_and_bodyless_lifecycle(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, _app, key, _caller = await setup_access(admin, app)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        created = await post(client, key)
        assert created.status_code == 201, created.text
        file = created.json()
        assert file['purpose'] == 'knowledge' and file['original_filename'] == 'source.txt'
        assert 'storage_key' not in file
        tag = created.headers['etag']
        url = '/api/v1/files/' + file['id']
        listed = await client.get('/api/v1/files', headers=auth(key))
        assert listed.json()['items'] == [file] and listed.json()['has_more'] is False
        detail = await client.get(url, headers=auth(key))
        assert detail.json() == file and detail.headers['etag'] == tag
        content = await client.get(url + '/content', headers={**auth(key), 'Range': 'bytes=1-2'})
        assert content.status_code == 206 and content.content == b'ou'
        assert content.headers['content-range'] == 'bytes 1-2/4'
        assert content.headers['x-content-type-options'] == 'nosniff'
        envelope(await client.delete(url, headers=auth(key)), 428)
        envelope(await client.delete(url, headers={**auth(key), 'If-Match': '"file-999"'}), 412)
        deleted = await client.delete(url, headers={**auth(key), 'If-Match': tag})
        assert deleted.status_code == 200 and deleted.json()['status'] == 'deleted', deleted.text
        assert deleted.headers['etag'] != tag
        assert (await client.get('/api/v1/files', headers=auth(key))).json()['items'] == []
        restored = await client.post(url + '/restore', headers={**auth(key), 'If-Match': deleted.headers['etag']})
        assert restored.status_code == 200 and restored.json()['status'] == 'active', restored.text
    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Invocation)) == 0
        assert await session.scalar(select(func.count()).select_from(KeyMinuteBucket)) == 0


async def test_resource_file_bearer_isolation_and_transport_errors(admin: httpx.AsyncClient, app: FastAPI) -> None:
    _c, _app, key, _caller = await setup_access(admin, app)
    created = await post(admin, key)
    assert created.status_code == 201, created.text
    url = '/api/v1/files/' + created.json()['id']
    # A valid Console cookie never substitutes for Bearer.
    missing = await admin.get(url)
    envelope(missing, 401)
    assert missing.headers['www-authenticate'] == 'Bearer'
    second = await application(admin)
    second_key = await issue(admin, second['id'], scopes=['files:read'])
    envelope(await admin.get(url, headers=auth(second_key)), 404)
    envelope(await post(admin, second_key), 403)
    envelope(await post(admin, key, data={'purpose': 'knowledge', 'owner_id': str(uuid4())}), 422)
    envelope(await post(admin, key, files=[('file', ('a.txt', b'a')), ('file', ('b.txt', b'b'))]), 422)
    envelope(await post(admin, key, files={'file': ('big.txt', b'x' * (20 * 1024 * 1024 + 1))}), 413)
    envelope(await admin.post('/api/v1/files', headers={**auth(key), 'Content-Type': 'application/json'}, json={}), 415)
    envelope(await admin.get('/api/v1/missing', headers=auth(key)), 404)
    envelope(await admin.get('/api/v1/files/not-a-uuid', headers=auth(key)), 422)


async def test_resource_file_idempotency_concurrency_semantics_and_current_authority(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, owner_app, key, caller = await setup_access(admin, app)
    external = uuid4()
    responses = await asyncio.gather(*(post(admin, key, external) for _ in range(4)))
    assert {r.status_code for r in responses} == {201}, [r.text for r in responses]
    file = responses[0].json()
    assert all(r.json() == file for r in responses)
    envelope(await post(admin, key, external, files={'file': ('different.txt', b'four', 'text/plain')}), 409)
    envelope(await post(admin, key, external, data={'purpose': 'skill'}), 409)
    async with c.database.sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(FileRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(APIRequest)) == 1
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (1, 4)
        row = await session.scalar(select(APIRequest))
        row.snapshot_expires_at = utcnow() - timedelta(hours=25)
    expired = await post(admin, key, external)
    envelope(expired, 409)
    assert expired.json()['error']['details']['result_id'] == file['id']
    assert (await admin.get('/api/v1/files/' + file['id'], headers=auth(key))).status_code == 200
    async with c.database.sessions.begin() as session:
        grants_app = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
        await c.resource_access.unbind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'file',
            UUID(file['id']),
            expected_version=grants_app.version,
        )
    envelope(await post(admin, key, external), 404)


async def test_resource_file_cursor_is_bound_and_filters_acl_before_limit(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, _owner_app, key, _caller = await setup_access(admin, app)
    files = [(await post(admin, key)).json() for _ in range(4)]
    assert all('id' in f for f in files), files
    async with c.database.sessions.begin() as session:
        rows = list(await session.scalars(select(FileRecord)))
        for row in rows:
            row.created_at = rows[0].created_at
    first = await admin.get('/api/v1/files?limit=2&purpose=knowledge', headers=auth(key))
    assert first.status_code == 200, first.text
    assert len(first.json()['items']) == 2 and first.json()['has_more']
    cursor = first.json()['next_cursor']
    next_page = await admin.get(
        '/api/v1/files', params={'limit': 2, 'purpose': 'knowledge', 'cursor': cursor}, headers=auth(key)
    )
    assert len(next_page.json()['items']) == 2 and not next_page.json()['has_more']
    assert len({f['id'] for f in first.json()['items'] + next_page.json()['items']}) == 4
    envelope(await admin.get('/api/v1/files', params={'cursor': cursor, 'purpose': 'skill'}, headers=auth(key)), 422)
    other = await application(admin)
    other_key = await issue(admin, other['id'], scopes=['files:read'])
    envelope(
        await admin.get('/api/v1/files', params={'cursor': cursor, 'purpose': 'knowledge'}, headers=auth(other_key)),
        422,
    )
    envelope(await admin.get('/api/v1/files?limit=101', headers=auth(key)), 422)


async def test_actual_console_purge_releases_enrollment_after_trash_and_unbind(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, owner_app, key, caller = await setup_access(admin, app)
    async with c.database.sessions.begin() as session:
        await c.resource_grants.configure_quota(
            session, caller.principal, UUID(owner_app['id']), file_limit=1, byte_limit=4, expected_version=1
        )
    first = await post(admin, key)
    assert first.status_code == 201, first.text
    file = first.json()
    envelope(await post(admin, key), 409)
    deleted = await admin.request('DELETE', '/api/files/' + file['id'], json={'expected_version': file['version']})
    assert deleted.status_code == 200, deleted.text
    async with c.database.sessions.begin() as session:
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (1, 4)
    purged = await admin.post(
        '/api/files/' + file['id'] + '/purge', json={'expected_version': deleted.json()['version']}
    )
    assert purged.status_code == 200, purged.text
    async with c.database.sessions.begin() as session:
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (0, 0)
    assert (await post(admin, key)).status_code == 201


async def test_resource_file_openapi_has_actual_multipart_and_bearer(app: FastAPI) -> None:
    spec = app.openapi()
    route = spec['paths']['/api/v1/files']['post']
    body = route['requestBody']['content']['multipart/form-data']['schema']
    schema = spec['components']['schemas'][body['$ref'].rsplit('/', 1)[1]]
    assert schema['properties']['file']['type'] == 'string'
    assert schema['properties']['file']['format'] == 'binary'
    assert schema['properties']['purpose']['type'] == 'string'
    assert route['security'] == [{'ResourceBearer': []}]
    assert route['x-required-scopes'] == ['files:write']


async def test_native_purge_hook_includes_unbound_app_and_is_retryable(admin: httpx.AsyncClient, app: FastAPI) -> None:
    c, owner_app, _key, caller = await setup_access(admin, app)
    file = await upload(admin)
    async with c.database.sessions.begin() as session:
        await c.resource_access.bind(
            session, caller.principal, UUID(owner_app['id']), 'file', UUID(file['id']), ['read'], expected_version=1
        )
        await c.resource_access.unbind(
            session, caller.principal, UUID(owner_app['id']), 'file', UUID(file['id']), expected_version=2
        )
    deleted = await admin.request('DELETE', '/api/files/' + file['id'], json={'expected_version': file['version']})
    assert deleted.status_code == 200, deleted.text
    await c.file_jobs.purge(caller.principal, UUID(file['id']), deleted.json()['version'])
    async with c.database.sessions() as session:
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (0, 0)
    await c.file_jobs.purge(caller.principal, UUID(file['id']), deleted.json()['version'])
    async with c.database.sessions() as session:
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (0, 0)


async def test_file_registration_rolls_back_own_object_and_revalidates_after_io(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: Any
) -> None:
    from zhiheng.shared.errors import AppError

    c, owner_app, key, caller = await setup_access(admin, app)
    existing = await upload(admin)
    before = await c.files.storage.entries(limit=100)
    real_put, real_autobind = c.files.storage.put, c.resource_access.autobind

    async def failing_bind(*args: Any, **kwargs: Any) -> Any:
        await real_autobind(*args, **kwargs)
        raise AppError('test_registration_failure', 'injected registration rollback', 503)

    monkeypatch.setattr(c.resource_access, 'autobind', failing_bind)
    failed = await post(admin, key)
    envelope(failed, 503)
    assert await c.files.storage.entries(limit=100) == before
    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(FileRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(APIRequest)) == 0
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (0, 0)
    monkeypatch.setattr(c.resource_access, 'autobind', real_autobind)

    async def revoke_during_put(data: bytes) -> str:
        stored = await real_put(data)
        # A different transaction can acquire Key/App locks during storage IO.
        async with c.database.sessions.begin() as session:
            await c.keys.revoke(session, caller.principal, UUID(owner_app['id']), UUID(key['id']))
        return stored

    monkeypatch.setattr(c.files.storage, 'put', revoke_during_put)
    envelope(await post(admin, key), 401)
    assert await c.files.storage.entries(limit=100) == before
    assert (await admin.get('/api/files/' + existing['id'] + '/download')).content == b'four'


async def test_concurrent_file_capacity_boundary_and_replay_discard_only_own_blob(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, owner_app, key, caller = await setup_access(admin, app)
    async with c.database.sessions.begin() as session:
        await c.resource_grants.configure_quota(
            session, caller.principal, UUID(owner_app['id']), file_limit=1, byte_limit=4, expected_version=1
        )
    results = await asyncio.gather(post(admin, key), post(admin, key))
    assert sorted(r.status_code for r in results) == [201, 409], [r.text for r in results]
    assert len(await c.files.storage.entries(limit=100)) == 1
    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(FileRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(APIRequest)) == 1
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (1, 4)


async def test_content_io_has_no_file_locks_and_rechecks_acl(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: Any
) -> None:
    c, owner_app, key, caller = await setup_access(admin, app)
    response = await post(admin, key)
    file_id = UUID(response.json()['id'])
    real_read = c.files.storage.read

    async def unbind_during_read(storage_key: str) -> bytes:
        data = await real_read(storage_key)
        async with c.database.sessions.begin() as session:
            await session.execute(select(FileRecord).where(FileRecord.id == file_id).with_for_update(nowait=True))
            app_info = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
            await c.resource_access.unbind(
                session, caller.principal, UUID(owner_app['id']), 'file', file_id, expected_version=app_info.version
            )
        return data

    monkeypatch.setattr(c.files.storage, 'read', unbind_during_read)
    envelope(await admin.get('/api/v1/files/' + str(file_id) + '/content', headers=auth(key)), 404)


async def test_cursor_acl_revoked_rows_do_not_consume_page_slots(admin: httpx.AsyncClient, app: FastAPI) -> None:
    from tests.core.integration.test_permission_tree import allow

    await allow(app, 'developer', 'create', 'key_create', 'invoke', 'grant')
    c = app.state.container
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        assert (
            await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        ).status_code == 200
        c, owner_app, key, caller = await setup_access(member, app)
        files = [await upload(admin) for _ in range(5)]
        version = 1
        for file in files:
            acl = await admin.patch(
                '/api/files/' + file['id'] + '/acl',
                json={
                    'expected_version': 1,
                    'access': 'private',
                    'entries': [{'user_id': str(caller.principal.id), 'effect': 'allow'}],
                },
            )
            assert acl.status_code == 200, acl.text
            async with c.database.sessions.begin() as session:
                bound = await c.resource_access.bind(
                    session,
                    caller.principal,
                    UUID(owner_app['id']),
                    'file',
                    UUID(file['id']),
                    ['read'],
                    expected_version=version,
                )
                version = bound.application_version
        for file in files[2:]:
            assert (
                await admin.patch(
                    '/api/files/' + file['id'] + '/acl',
                    json={'expected_version': 2, 'access': 'private', 'entries': []},
                )
            ).status_code == 200
        response = await member.get('/api/v1/files?limit=2', headers=auth(key))
        assert response.status_code == 200, response.text
        assert {f['id'] for f in response.json()['items']} == {f['id'] for f in files[:2]}
        assert response.json()['has_more'] is False


async def test_ambiguous_commit_never_deletes_a_registered_object(admin: httpx.AsyncClient, app: FastAPI) -> None:
    import pytest
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from zhiheng.shared.errors import AppError

    c, _owner_app, _key, caller = await setup_access(admin, app)

    def mark_registration(session: Session, _context: Any) -> None:
        if any(isinstance(row, FileRecord) for row in session.new):
            session.info['lost_registration_ack'] = True

    def lose_ack(session: Session) -> None:
        if session.info.pop('lost_registration_ack', False):
            raise AppError('commit_ack_lost', 'test commit acknowledgement lost', 503)

    event.listen(Session, 'after_flush', mark_registration)
    event.listen(Session, 'after_commit', lose_ack)
    try:
        with pytest.raises(AppError):
            await c.file_commands.upload(
                caller.principal,
                filename='committed.txt',
                content=b'four',
                purpose='knowledge',
                caller=caller,
                idempotency_key=uuid4(),
            )
    finally:
        event.remove(Session, 'after_flush', mark_registration)
        event.remove(Session, 'after_commit', lose_ack)
    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(FileRecord)) == 1
    assert len(await c.files.storage.entries(limit=100)) == 1


async def test_reconcile_repairs_failed_post_purge_callback_without_releasing_other_source(
    admin: httpx.AsyncClient, app: FastAPI, monkeypatch: Any
) -> None:
    import pytest

    from zhiheng.shared.errors import AppError

    c, owner_app, key, caller = await setup_access(admin, app)
    first, _second = (await post(admin, key)).json(), (await post(admin, key)).json()
    real_callback = c.file_jobs.after_purge
    called = False

    async def fail_once(file_id: UUID) -> None:
        nonlocal called
        if not called:
            called = True
            raise AppError('injected_accounting_failure', 'retry accounting', 503)
        await real_callback(file_id)

    monkeypatch.setattr(c.file_jobs, 'after_purge', fail_once)
    deleted = await admin.request('DELETE', '/api/files/' + first['id'], json={'expected_version': 1})
    with pytest.raises(AppError):
        await c.file_jobs.purge(caller.principal, UUID(first['id']), deleted.json()['version'])
    async with c.database.sessions() as session:
        assert (await c.files.accounting_fact(session, UUID(first['id']))).status == 'purged'
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (2, 8)
    await c.file_jobs.run(caller.principal, 'reconcile')
    async with c.database.sessions() as session:
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (1, 4)


async def test_denied_download_audits_invalid_bearer(admin: httpx.AsyncClient, app: FastAPI) -> None:
    from zhiheng.modules.files.orm import FileAudit

    c, _owner_app, key, _caller = await setup_access(admin, app)
    created = await post(admin, key)
    file_id = created.json()['id']
    denied = await admin.get('/api/v1/files/' + file_id + '/content')
    envelope(denied, 401)
    async with c.database.sessions() as session:
        audit = await session.scalar(
            select(FileAudit).where(FileAudit.file_id == UUID(file_id), FileAudit.action == 'download')
        )
        assert audit is not None and audit.details['outcome'] == 'denied' and audit.details['bytes_sent'] == 0


async def test_resource_post_rejects_duplicate_idempotency_headers(admin: httpx.AsyncClient, app: FastAPI) -> None:
    _c, _app, key, _caller = await setup_access(admin, app)
    duplicate = await admin.post(
        '/api/v1/files',
        files={'file': ('same.txt', b'a')},
        data={'purpose': 'knowledge'},
        headers=[
            ('Authorization', 'Bearer ' + key['api_key']),
            ('Idempotency-Key', str(uuid4())),
            ('Idempotency-Key', str(uuid4())),
        ],
    )
    envelope(duplicate, 422)


async def test_resource_cors_preflight_has_protocol_headers_and_request_identity(app: FastAPI) -> None:
    from zhiheng.api.main import create_app

    c = app.state.container
    cors_app = create_app(c.settings.model_copy(update={'allowed_origins': ('https://client.example',)}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cors_app), base_url='http://test') as client:
        preflight = await client.options(
            '/api/v1/files',
            headers={
                'Origin': 'https://client.example',
                'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'Authorization,Idempotency-Key,If-Match,Range',
            },
        )
        assert preflight.status_code == 200, preflight.text
        UUID(preflight.headers['X-Request-ID'])
        assert 'Idempotency-Key' in preflight.headers['access-control-allow-headers']


async def test_resource_upload_rejects_authority_fields_and_transport_overhead(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    _c, _owner_app, key, _caller = await setup_access(admin, app)
    for name in ('owner_id', 'is_admin', 'storage_key', 'path'):
        envelope(await post(admin, key, data={'purpose': 'knowledge', name: 'forged'}), 422)
    without_key = await admin.post(
        '/api/v1/files', headers=auth(key), files={'file': ('a.txt', b'a')}, data={'purpose': 'knowledge'}
    )
    assert without_key.status_code == 201, without_key.text
    envelope(
        await post(
            admin, key, files={'file': ('big.txt', b'x' * (20 * 1024 * 1024))}, data={'purpose': 'x' * (64 * 1024 + 1)}
        ),
        413,
    )
    envelope(
        await admin.post(
            '/api/v1/unknown',
            headers={**auth(key), 'Content-Type': 'application/json'},
            content=b'x' * (1024 * 1024 + 1),
        ),
        413,
    )
    created = await post(admin, key)
    invalid_range = await admin.get(
        '/api/v1/files/' + created.json()['id'] + '/content', headers={**auth(key), 'Range': 'bytes=99-'}
    )
    envelope(invalid_range, 416)
    assert invalid_range.headers['content-range'] == 'bytes */4'


async def test_resource_cursor_rejects_non_string_uuid_components(admin: httpx.AsyncClient, app: FastAPI) -> None:
    import base64
    import json

    _c, owner_app, key, _caller = await setup_access(admin, app)
    raw = [f'{owner_app["id"]}:file:purpose=', utcnow().isoformat(), 123]
    cursor = base64.urlsafe_b64encode(json.dumps(raw).encode()).decode()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test'
    ) as client:
        envelope(await client.get('/api/v1/files', params={'cursor': cursor}, headers=auth(key)), 422)


async def test_explicit_artifact_list_get_and_current_source_acl_before_limit(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, owner_app, key, caller = await setup_access(admin, app)
    source = (await post(admin, key)).json()
    other = (await post(admin, key)).json()
    prepared = await c.file_inputs.prepare(caller.principal, UUID(source['id']))
    artifact_id = prepared.summary.artifact_file_id
    assert str(artifact_id) not in {
        item['id'] for item in (await admin.get('/api/v1/files', headers=auth(key))).json()['items']
    }

    async def bind_artifact() -> None:
        async with c.database.sessions.begin() as session:
            current = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
            await c.resource_access.bind(
                session,
                caller.principal,
                UUID(owner_app['id']),
                'file',
                artifact_id,
                ['read'],
                expected_version=current.version,
            )
            assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (2, 8)

    await bind_artifact()
    artifact_url = '/api/v1/files/' + str(artifact_id)
    assert (await admin.get(artifact_url, headers=auth(key))).status_code == 200
    assert str(artifact_id) in {
        item['id'] for item in (await admin.get('/api/v1/files', headers=auth(key))).json()['items']
    }
    users = (await admin.get('/api/files/users')).json()
    member_id = next(user['id'] for user in users if user['username'] == 'member')
    transferred = await admin.post(
        '/api/files/' + source['id'] + '/transfer',
        json={
            'owner_id': member_id,
            'expected_version': source['version'],
            'reason': 'source ACL boundary',
            'uploader_access_enabled': False,
        },
    )
    assert transferred.status_code == 200, transferred.text
    from tests.core.integration.test_permission_tree import allow

    await allow(app, 'ai:storage', 'acl')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as source_owner:
        login = await source_owner.post(
            '/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'}
        )
        assert login.status_code == 200
        shared = await source_owner.patch(
            '/api/files/' + source['id'] + '/acl',
            json={
                'expected_version': transferred.json()['acl_version'],
                'access': 'private',
                'entries': [{'user_id': str(caller.principal.id), 'effect': 'allow'}],
            },
        )
        assert shared.status_code == 200, shared.text
        await bind_artifact()
        first = await admin.get('/api/v1/files?limit=1', headers=auth(key))
        assert [item['id'] for item in first.json()['items']] == [str(artifact_id)]
        assert (await admin.get(artifact_url, headers=auth(key))).status_code == 200
        revoked = await source_owner.patch(
            '/api/files/' + source['id'] + '/acl',
            json={'expected_version': shared.json()['file']['acl_version'], 'access': 'private', 'entries': []},
        )
        assert revoked.status_code == 200, revoked.text
        envelope(await admin.get(artifact_url, headers=auth(key)), 404)
        page = await admin.get('/api/v1/files?limit=1', headers=auth(key))
        assert [item['id'] for item in page.json()['items']] == [other['id']]
        assert not page.json()['has_more']


@pytest.mark.parametrize(
    ('origin', 'requested_method', 'requested_headers'),
    [
        ('https://unlisted.example', 'POST', 'Authorization'),
        ('https://client.example', 'TRACE', 'Authorization'),
        ('https://client.example', 'POST', 'Authorization,X-Unknown'),
    ],
    ids=['origin', 'method', 'headers'],
)
async def test_rejected_resource_cors_preflight_uses_common_error(
    app: FastAPI,
    origin: str,
    requested_method: str,
    requested_headers: str,
) -> None:
    from zhiheng.api.main import create_app

    c = app.state.container
    cors_app = create_app(c.settings.model_copy(update={'allowed_origins': ('https://client.example',)}))
    headers = {
        'Origin': origin,
        'Access-Control-Request-Method': requested_method,
        'Access-Control-Request-Headers': requested_headers,
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cors_app), base_url='http://test') as client:
        resource = await client.options('/api/v1/files', headers=headers)
        assert resource.headers['content-type'] == 'application/json', resource.text
        envelope(resource, 400)
        assert resource.json()['error']['code'] == 'cors_preflight_rejected'
        console = await client.options('/api/files', headers=headers)
        assert console.status_code == 400 and console.headers['content-type'].startswith('text/plain')
        assert console.text.startswith('Disallowed CORS ')
        for name in (
            'Vary',
            'Access-Control-Allow-Origin',
            'Access-Control-Allow-Credentials',
            'Access-Control-Allow-Methods',
            'Access-Control-Allow-Headers',
            'Access-Control-Max-Age',
        ):
            assert resource.headers.get(name) == console.headers.get(name), name


async def test_resource_file_without_key_is_a_new_submission(admin: httpx.AsyncClient, app: FastAPI) -> None:
    c, _owner_app, key, _caller = await setup_access(admin, app)
    responses = [
        await admin.post(
            '/api/v1/files',
            headers={**auth(key), 'X-Request-ID': 'same-client-trace'},
            files={'file': ('source.txt', b'four', 'text/plain')},
            data={'purpose': 'knowledge'},
        )
        for _ in range(2)
    ]
    assert [response.status_code for response in responses] == [201, 201], [r.text for r in responses]
    assert responses[0].json()['id'] != responses[1].json()['id']
    async with c.database.sessions() as session:
        requests = list(await session.scalars(select(APIRequest)))
        assert len(requests) == 2
        assert len({row.idempotency_key for row in requests}) == 2


@pytest.mark.parametrize('value', ['', 'not-a-uuid'])
async def test_resource_file_rejects_present_invalid_key(admin: httpx.AsyncClient, app: FastAPI, value: str) -> None:
    _c, _owner_app, key, _caller = await setup_access(admin, app)
    response = await admin.post(
        '/api/v1/files',
        headers={**auth(key), 'Idempotency-Key': value},
        files={'file': ('source.txt', b'four', 'text/plain')},
        data={'purpose': 'knowledge'},
    )
    envelope(response, 422)
