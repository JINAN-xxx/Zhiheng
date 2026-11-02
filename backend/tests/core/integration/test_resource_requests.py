"""Resource admission facts use application scope, permanent identities and source quotas."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.provider import ProviderServer
from zhiheng.application.access import CallerContext
from zhiheng.bootstrap.container import Container
from zhiheng.modules.developer.public import APIRequestInfo, ResourceGrantInfo
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


async def setup_access(
    admin: httpx.AsyncClient, app: FastAPI, scopes: list[str] | None = None
) -> tuple[Container, dict[str, Any], dict[str, Any], CallerContext]:
    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=scopes or ['files:read', 'files:write'])
    c = app.state.container
    async with c.database.sessions.begin() as session:
        caller = await c.resource_access.bearer(session, key['api_key'], scope='files:read')
    return c, owner_app, key, caller


async def test_permanent_identity_concurrency_conflict_expiry_and_cross_key(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, owner_app, key, caller = await setup_access(admin, app)
    external = uuid4()

    async def admit() -> APIRequestInfo:
        async with c.database.sessions.begin() as session:
            return await c.api_requests.begin(
                session, caller.identity, 'files.create', external, {'a': 1, 'b': 2}, scope='files:read'
            )

    results = await asyncio.gather(*(admit() for _ in range(6)))
    assert len({row.id for row in results}) == 1
    assert sum(not row.replayed for row in results) == 1
    request = results[0]
    async with c.database.sessions.begin() as session:
        await c.api_requests.complete(
            session, request.id, result_kind='file', result_id=uuid4(), snapshot={'accepted': True}
        )
    rotated = await issue(admin, owner_app['id'], scopes=['files:read'])
    async with c.database.sessions.begin() as session:
        current = await c.resource_access.bearer(session, rotated['api_key'], scope='files:read')
        replay = await c.api_requests.begin(
            session, current.identity, 'files.create', external, {'b': 2, 'a': 1}, scope='files:read'
        )
        assert replay.key_id == UUID(key['id']) and replay.snapshot == {'accepted': True}
        assert replay.native_request_id == request.native_request_id
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as mismatch:
            await c.api_requests.begin(session, caller.identity, 'files.create', external, {'a': 2}, scope='files:read')
        assert mismatch.value.status_code == 409
    from zhiheng.modules.developer.orm import APIRequest

    async with c.database.sessions.begin() as session:
        row = await session.get(APIRequest, request.id)
        row.snapshot_expires_at = utcnow() - timedelta(seconds=1)
    async with c.database.sessions.begin() as session:
        replay = await c.api_requests.begin(
            session, current.identity, 'files.create', external, {'a': 1, 'b': 2}, scope='files:read'
        )
        assert replay.snapshot_expired and replay.result_id is not None
        assert replay.snapshot is None and replay.replayed
        assert (await session.scalar(select(func.count()).select_from(APIRequest))) == 1


async def test_resource_rpm_does_not_use_token_buckets(admin: httpx.AsyncClient, app: FastAPI) -> None:
    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['files:read'], resource_rpm_limit=1)
    c = app.state.container
    async with c.database.sessions.begin() as session:
        caller = await c.resource_access.bearer(session, key['api_key'], scope='files:read')
        await c.resource_limits.consume(session, caller.identity)
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as limited:
            await c.resource_limits.consume(session, caller.identity)
        assert limited.value.status_code == 429 and limited.value.details['retry_after'] > 0
    from zhiheng.modules.developer.orm import Invocation, KeyMinuteBucket

    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Invocation)) == 0
        assert await session.scalar(select(func.count()).select_from(KeyMinuteBucket)) == 0


async def upload(client: Any, content: Any = b'four') -> dict[str, Any]:
    response = await client.post(
        '/api/files?filename=source.txt', content=content, headers={'Content-Type': 'application/octet-stream'}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_app_grants_cas_isolation_current_acl_and_owner_forgery(admin: httpx.AsyncClient, app: FastAPI) -> None:
    import httpx

    c, owner_app, _key, caller = await setup_access(admin, app)
    file = await upload(admin)
    second = await application(admin)
    second_key = await issue(admin, second['id'], scopes=['files:read'])
    async with c.database.sessions.begin() as session:
        granted = await c.resource_access.bind(
            session, caller.principal, UUID(owner_app['id']), 'file', UUID(file['id']), ['read'], expected_version=1
        )
        assert granted.actions == ('read',)
    async with c.database.sessions.begin() as session:
        await c.resource_access.require(session, caller, 'file', UUID(file['id']), 'read')
        with pytest.raises(AppError) as error:
            await c.resource_access.require(session, caller, 'file', UUID(file['id']), 'write')
        assert error.value.status_code == 404
        other = await c.resource_access.bearer(session, second_key['api_key'], scope='files:read')
        with pytest.raises(AppError) as error:
            await c.resource_access.require(session, other, 'file', UUID(file['id']), 'read')
        assert error.value.status_code == 404
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as error:
            await c.resource_access.bind(
                session,
                caller.principal,
                UUID(owner_app['id']),
                'file',
                UUID(file['id']),
                ['write'],
                expected_version=1,
            )
        assert error.value.status_code == 409
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        theirs = await upload(member)
        async with c.database.sessions.begin() as session:
            with pytest.raises(AppError) as error:
                await c.resource_access.bind(
                    session,
                    caller.principal,
                    UUID(owner_app['id']),
                    'file',
                    UUID(theirs['id']),
                    ['read'],
                    expected_version=2,
                )
            assert error.value.status_code == 404
        foreign_app = await application(member)
        async with c.database.sessions.begin() as session:
            with pytest.raises(AppError) as error:
                await c.resource_access.bind(
                    session,
                    caller.principal,
                    UUID(foreign_app['id']),
                    'file',
                    UUID(file['id']),
                    ['read'],
                    expected_version=1,
                )
            assert error.value.status_code == 404


async def test_shared_file_read_delegation_never_expands_to_write_and_revocation_is_current(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    import httpx

    file = await upload(admin)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        c, owner_app, _key, caller = await setup_access(member, app)
        acl = await admin.patch(
            f'/api/files/{file["id"]}/acl',
            json={
                'expected_version': 1,
                'access': 'private',
                'entries': [{'user_id': str(caller.principal.id), 'effect': 'allow'}],
            },
        )
        assert acl.status_code == 200, acl.text
        async with c.database.sessions.begin() as session:
            await c.resource_access.bind(
                session, caller.principal, UUID(owner_app['id']), 'file', UUID(file['id']), ['read'], expected_version=1
            )
        async with c.database.sessions.begin() as session:
            with pytest.raises(AppError) as error:
                await c.resource_access.bind(
                    session,
                    caller.principal,
                    UUID(owner_app['id']),
                    'file',
                    UUID(file['id']),
                    ['write'],
                    expected_version=2,
                )
            assert error.value.status_code == 404
        async with c.database.sessions.begin() as session:
            await c.resource_access.require(session, caller, 'file', UUID(file['id']), 'read')
        revoked = await admin.patch(
            f'/api/files/{file["id"]}/acl', json={'expected_version': 2, 'access': 'private', 'entries': []}
        )
        assert revoked.status_code == 200, revoked.text
        async with c.database.sessions.begin() as session:
            with pytest.raises(AppError) as error:
                await c.resource_access.require(session, caller, 'file', UUID(file['id']), 'read')
            assert error.value.status_code == 404


async def test_source_quota_dedup_trash_unbind_purge_and_atomic_overflow(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, owner_app, _key, caller = await setup_access(admin, app)
    first, second = await upload(admin), await upload(admin)
    async with c.database.sessions.begin() as session:
        await c.resource_grants.configure_quota(
            session, caller.principal, UUID(owner_app['id']), file_limit=1, byte_limit=4, expected_version=1
        )
        await c.resource_access.bind(
            session, caller.principal, UUID(owner_app['id']), 'file', UUID(first['id']), ['read'], expected_version=2
        )
        await c.resource_access.bind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'file',
            UUID(first['id']),
            ['read', 'write'],
            expected_version=3,
        )
        usage = await c.resource_grants.source_usage(session, UUID(owner_app['id']))
        assert usage == (1, 4)
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as error:
            await c.resource_access.bind(
                session,
                caller.principal,
                UUID(owner_app['id']),
                'file',
                UUID(second['id']),
                ['read'],
                expected_version=4,
            )
        assert error.value.status_code == 409
    deleted = await admin.request('DELETE', f'/api/files/{first["id"]}', json={'expected_version': 1})
    assert deleted.status_code == 200, deleted.text
    async with c.database.sessions.begin() as session:
        await c.resource_access.unbind(
            session, caller.principal, UUID(owner_app['id']), 'file', UUID(first['id']), expected_version=4
        )
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (1, 4)
        with pytest.raises(AppError):
            await c.resource_access.release_purged(session, UUID(owner_app['id']), UUID(first['id']))
    # Actual purge must be established by the file domain; no grant/unbind operation releases enrollment.
    await c.file_jobs.purge(caller.principal, UUID(first['id']), deleted.json()['version'])
    async with c.database.sessions.begin() as session:
        await c.resource_access.release_purged(session, UUID(owner_app['id']), UUID(first['id']))
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (0, 0)


async def test_console_grant_contract_rejects_forged_owner_and_scope_catalog(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    file = await upload(admin)
    owner_app = await application(admin)
    catalog = await admin.get('/api/developer/applications/scope-catalog')
    assert catalog.status_code == 200, catalog.text
    assert 'files:write' in catalog.json()['scopes']
    base = f'/api/developer/applications/{owner_app["id"]}/resources'
    forged = await admin.put(
        base + f'/file/{file["id"]}',
        json={'actions': ['read'], 'expected_version': 1, 'owner_id': owner_app['owner_id']},
    )
    assert forged.status_code == 422, forged.text
    bound = await admin.put(base + f'/file/{file["id"]}', json={'actions': ['read'], 'expected_version': 1})
    assert bound.status_code == 200, bound.text
    assert (await admin.get(base)).json()[0]['resource_id'] == file['id']
    unbound = await admin.request('DELETE', base + f'/file/{file["id"]}', json={'expected_version': 2})
    assert unbound.status_code == 200, unbound.text
    assert (await admin.get(base)).json() == []


async def test_request_replay_authorizes_current_targets_before_expired_reference(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, owner_app, _key, caller = await setup_access(admin, app)
    file = await upload(admin)
    external = uuid4()
    async with c.database.sessions.begin() as session:
        await c.resource_access.bind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'file',
            UUID(file['id']),
            ['read', 'write'],
            expected_version=1,
        )
        original = await c.resource_access.begin_request(
            session,
            caller,
            'files.update',
            external,
            {'name': 'x'},
            scope='files:write',
            resources=({'kind': 'file', 'id': file['id'], 'action': 'write'},),
        )
        await c.api_requests.complete(
            session, original.id, result_kind='file', result_id=UUID(file['id']), snapshot={'name': 'x'}
        )
    from zhiheng.modules.developer.orm import APIRequest

    async with c.database.sessions.begin() as session:
        row = await session.get(APIRequest, original.id)
        row.snapshot_expires_at = utcnow() - timedelta(hours=1)
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as error:
            await c.resource_access.begin_request(
                session,
                caller,
                'files.update',
                external,
                {'name': 'x'},
                scope='files:write',
                resources=({'kind': 'file', 'id': file['id'], 'action': 'write'},),
            )
        assert error.value.status_code == 409 and error.value.details['result_id'] == file['id']
    async with c.database.sessions.begin() as session:
        await c.resource_access.unbind(
            session, caller.principal, UUID(owner_app['id']), 'file', UUID(file['id']), expected_version=2
        )
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as hidden:
            await c.resource_access.begin_request(
                session,
                caller,
                'files.update',
                external,
                {'name': 'x'},
                scope='files:write',
                resources=({'kind': 'file', 'id': file['id'], 'action': 'write'},),
            )
        assert hidden.value.status_code == 404 and hidden.value.details is None


async def test_bearer_dependency_is_strict_and_cookie_context_is_frozen(admin: httpx.AsyncClient, app: FastAPI) -> None:
    from dataclasses import FrozenInstanceError

    from starlette.requests import Request

    from zhiheng.api.resources.dependencies import resource_caller

    c, _owner_app, key, caller = await setup_access(admin, app)
    dependency = resource_caller('files:read')
    for headers in (
        [],
        [(b'authorization', b'Bearer invalid')],
        [(b'authorization', f'Bearer {key["api_key"]}'.encode())] * 2,
    ):
        async with c.database.sessions.begin() as session:
            with pytest.raises(AppError) as error:
                await dependency(Request({'type': 'http', 'headers': headers}), c, session)
            assert error.value.status_code == 401
    with pytest.raises(FrozenInstanceError):
        caller.principal = None


async def test_rejected_resource_scope_consumes_only_resource_rpm(admin: httpx.AsyncClient, app: FastAPI) -> None:
    from starlette.requests import Request

    from zhiheng.api.resources.dependencies import resource_caller
    from zhiheng.modules.developer.orm import KeyMinuteBucket, ResourceMinuteBucket

    c, _owner_app, key, _caller = await setup_access(admin, app, ['files:read'])
    headers = [(b'authorization', f'Bearer {key["api_key"]}'.encode())]
    async with c.database.sessions() as session:
        with pytest.raises(AppError) as denied:
            await resource_caller('files:write')(Request({'type': 'http', 'headers': headers}), c, session)
        assert denied.value.status_code == 403
    async with c.database.sessions() as session:
        count = await session.scalar(select(func.sum(ResourceMinuteBucket.requests)))
        assert count == 1
        assert await session.scalar(select(func.count()).select_from(KeyMinuteBucket)) == 0


async def test_two_apps_same_owner_same_external_key_have_distinct_native_identities(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, _first, _key, caller = await setup_access(admin, app)
    second = await application(admin)
    key = await issue(admin, second['id'], scopes=['files:read'])
    external = uuid4()
    async with c.database.sessions.begin() as session:
        left = await c.resource_access.begin_request(
            session, caller, 'files.create', external, {'name': 'same'}, scope='files:read'
        )
    async with c.database.sessions.begin() as session:
        other = await c.resource_access.bearer(session, key['api_key'], scope='files:read')
        right = await c.resource_access.begin_request(
            session, other, 'files.create', external, {'name': 'same'}, scope='files:read'
        )
    assert left.owner_id == right.owner_id
    assert left.id != right.id and left.native_request_id != right.native_request_id


async def test_concurrent_quota_bind_admits_only_one_original_and_derived_is_excluded(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, owner_app, _key, caller = await setup_access(admin, app)
    first, second = await upload(admin), await upload(admin)
    async with c.database.sessions.begin() as session:
        await c.resource_grants.configure_quota(
            session, caller.principal, UUID(owner_app['id']), file_limit=1, byte_limit=4, expected_version=1
        )

    async def bind(file: dict[str, Any]) -> ResourceGrantInfo:
        async with c.database.sessions.begin() as session:
            return await c.resource_access.bind(
                session, caller.principal, UUID(owner_app['id']), 'file', UUID(file['id']), ['read'], expected_version=2
            )

    results = await asyncio.gather(bind(first), bind(second), return_exceptions=True)
    assert sum(isinstance(result, AppError) and result.status_code == 409 for result in results) == 1
    admitted = first if not isinstance(results[0], Exception) else second
    prepared = await c.file_inputs.prepare(caller.principal, UUID(admitted['id']))
    async with c.database.sessions.begin() as session:
        await c.resource_access.bind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'file',
            prepared.summary.artifact_file_id,
            ['read'],
            expected_version=3,
        )
        assert await c.resource_grants.source_usage(session, UUID(owner_app['id'])) == (1, 4)


async def test_server_request_id_is_stable_and_unresolved_admission_is_explicit(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    c, _app, _key, caller = await setup_access(admin, app)
    assert isinstance(caller.request_id, UUID)
    async with c.database.sessions.begin() as session:
        current = await c.resource_access.current(session, caller, 'files:read')
        assert current.request_id == caller.request_id
        info = await c.resource_access.begin_request(session, caller, 'files.create', uuid4(), {}, scope='files:read')
        assert info.result_pending
        completed = await c.api_requests.complete(session, info.id, result_kind='file', result_id=uuid4(), snapshot={})
        assert not completed.result_pending


async def test_autobind_never_claims_shared_knowledge_but_explicit_read_query_binding_works(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    import httpx

    from tests.core.integration.test_knowledge import embedding_model

    model = await embedding_model(admin, provider)
    c, owner_app, _key, caller = await setup_access(
        admin, app, ['files:read', 'knowledge:read', 'knowledge:write', 'knowledge:query']
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        response = await member.post('/api/knowledge/bases', json={'name': 'Shared', 'embed_model_id': model})
        assert response.status_code == 201, response.text
        base = response.json()
        shared = await member.patch(
            f'/api/knowledge/bases/{base["id"]}/acl',
            json={
                'access': 'private',
                'expected_version': base['acl_version'],
                'entries': [{'user_id': str(caller.principal.id), 'effect': 'allow'}],
            },
        )
        assert shared.status_code == 200, shared.text
    async with c.database.sessions.begin() as session:
        await c.resource_access.bind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'knowledge_base',
            UUID(base['id']),
            ['read', 'query'],
            expected_version=1,
        )
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as error:
            await c.resource_access.autobind(session, caller, 'knowledge_base', UUID(base['id']))
        assert error.value.status_code == 404
        await c.resource_access.require(session, caller, 'knowledge_base', UUID(base['id']), 'query', lock=False)


async def test_expired_snapshot_does_not_block_authorized_native_fact_reads(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    from zhiheng.modules.developer.orm import APIRequest

    c, owner_app, _key, caller = await setup_access(admin, app)
    file = await upload(admin)
    external = uuid4()
    targets = ({'kind': 'file', 'id': file['id'], 'action': 'read'},)
    async with c.database.sessions.begin() as session:
        await c.resource_access.bind(
            session, caller.principal, UUID(owner_app['id']), 'file', UUID(file['id']), ['read'], expected_version=1
        )
        original = await c.resource_access.begin_request(
            session, caller, 'files.inspect', external, {'file_id': file['id']}, scope='files:read', resources=targets
        )
        await c.api_requests.complete(session, original.id, result_kind='file', result_id=UUID(file['id']), snapshot={})
    async with c.database.sessions.begin() as session:
        row = await session.get(APIRequest, original.id)
        row.snapshot_expires_at = utcnow() - timedelta(hours=1)
    async with c.database.sessions.begin() as session:
        current = await c.api_requests.get(session, UUID(owner_app['id']), original.id)
        await c.resource_access.authorize_request(session, caller, current)
        assert current.snapshot_expired and current.snapshot is None and current.result_id == UUID(file['id'])
        with pytest.raises(AppError) as replay:
            await c.resource_access.begin_request(
                session,
                caller,
                'files.inspect',
                external,
                {'file_id': file['id']},
                scope='files:read',
                resources=targets,
            )
        assert replay.value.status_code == 409 and replay.value.details['result_id'] == file['id']


async def test_idempotency_semantics_include_target_resource_identity(admin: httpx.AsyncClient, app: FastAPI) -> None:
    c, owner_app, _key, caller = await setup_access(admin, app)
    first, second = await upload(admin), await upload(admin)
    external = uuid4()
    async with c.database.sessions.begin() as session:
        for version, file in enumerate((first, second), start=1):
            await c.resource_access.bind(
                session,
                caller.principal,
                UUID(owner_app['id']),
                'file',
                UUID(file['id']),
                ['read'],
                expected_version=version,
            )
        await c.resource_access.begin_request(
            session,
            caller,
            'files.inspect',
            external,
            {},
            scope='files:read',
            resources=({'kind': 'file', 'id': first['id'], 'action': 'read'},),
        )
    async with c.database.sessions.begin() as session:
        with pytest.raises(AppError) as different:
            await c.resource_access.begin_request(
                session,
                caller,
                'files.inspect',
                external,
                {},
                scope='files:read',
                resources=({'kind': 'file', 'id': second['id'], 'action': 'read'},),
            )
        assert different.value.status_code == 409


async def test_file_transfer_invalidates_old_binding_even_when_acl_remains_and_owner_returns(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    from zhiheng.modules.identity.public import IdentityService

    c = app.state.container
    file = await upload(admin)
    async with c.database.sessions.begin() as session:
        third = await IdentityService().create_user(
            session, username='transfer-target', password='platform-test-password', is_admin=False
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as member:
        await member.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
        c, owner_app, _key, caller = await setup_access(member, app)
        acl = await admin.patch(
            f'/api/files/{file["id"]}/acl',
            json={
                'expected_version': 1,
                'access': 'private',
                'entries': [{'user_id': str(caller.principal.id), 'effect': 'allow'}],
            },
        )
        assert acl.status_code == 200, acl.text
        async with c.database.sessions.begin() as session:
            await c.resource_access.bind(
                session, caller.principal, UUID(owner_app['id']), 'file', UUID(file['id']), ['read'], expected_version=1
            )
        current = (await admin.get(f'/api/files/{file["id"]}')).json()
        for new_owner in (str(third.id), file['owner_id']):
            transferred = await admin.post(
                f'/api/files/{file["id"]}/transfer',
                json={
                    'owner_id': new_owner,
                    'expected_version': current['version'],
                    'reason': 'ownership boundary probe',
                    'uploader_access_enabled': False,
                },
            )
            assert transferred.status_code == 200, transferred.text
            current = transferred.json()
            assert (await member.get(f'/api/files/{file["id"]}')).status_code == 200
            async with c.database.sessions.begin() as session:
                with pytest.raises(AppError) as stale:
                    await c.resource_access.require(session, caller, 'file', UUID(file['id']), 'read')
                assert stale.value.status_code == 404
        async with c.database.sessions.begin() as session:
            await c.resource_access.bind(
                session, caller.principal, UUID(owner_app['id']), 'file', UUID(file['id']), ['read'], expected_version=2
            )
            await c.resource_access.require(session, caller, 'file', UUID(file['id']), 'read')
