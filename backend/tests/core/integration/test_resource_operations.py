"""Native operation execution identity, price admission, cancellation and revocation."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.core.integration.test_knowledge_milvus import real_vectors  # noqa: F401
from tests.core.integration.test_resource_files import auth, envelope, post
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.integration.test_resource_knowledge import SCOPES, finish, setup
from tests.core.integration.test_work import member_client
from tests.core.knowledge_support import knowledge_models, legacy_grant
from tests.core.provider import ProviderServer
from zhiheng.bootstrap.container import Container
from zhiheng.modules.billing.orm import Invocation
from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeJob
from zhiheng.modules.knowledge.public import BuildClaim
from zhiheng.modules.models.public import ModelBinding
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


async def revoke(admin: httpx.AsyncClient, owner_app: dict, key: dict) -> None:
    result = await admin.post(
        '/api/developer/applications/' + owner_app['id'] + '/keys/' + key['id'] + '/revoke', json={}
    )
    assert result.status_code == 200, result.text


async def set_legacy_policy(app: FastAPI, base_id: str) -> None:
    # Construct pre-v2 bases before importing any source whose current file ACL remains authoritative.
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(KnowledgeBase, UUID(base_id))
        assert row is not None
        row.document_policy = 'legacy'


@pytest.mark.parametrize('at_model', [False, True])
async def test_original_execution_key_revoked_while_other_key_can_observe(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, at_model: bool
) -> None:
    owner_app, key, base = await setup(admin, provider, app)
    observer = await issue(admin, owner_app['id'], scopes=SCOPES)
    file = await post(admin, key)
    operation = await admin.post(
        '/api/v1/knowledge-bases/' + base['id'] + '/documents',
        headers=auth(key, uuid4()),
        json={'file_ids': [file.json()['id']]},
    )
    assert operation.status_code == 202, operation.text
    c = app.state.container
    native_id = UUID(operation.json()['native_id'])
    if at_model:
        for _ in range(3):
            await c.knowledge_jobs.tick(native_id)
        async with c.database.sessions() as session:
            child = await session.scalar(select(Run.id).where(Run.status == 'pending'))
        assert child is not None
    await revoke(admin, owner_app, key)
    if at_model:
        await execute_run(c, child)
    terminal = await finish(admin, app, observer, operation.json())
    assert terminal['status'] == 'failed' and provider.requests == [], str(terminal)
    async with c.database.sessions() as session:
        invocations = list(await session.scalars(select(Invocation)))
        assert len(invocations) == int(at_model)
        assert all(row.key_id == UUID(key['id']) and row.status != 'pending' for row in invocations)


@pytest.mark.parametrize('change', ['no_rate', 'currency', 'zero_budget'])
async def test_unbilled_native_model_steps_do_not_require_price_or_usage_budget(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, change: str
) -> None:
    from sqlalchemy import delete

    from zhiheng.modules.models.orm import RateCard

    owner_app, key, base = await setup(admin, provider, app)
    if change == 'no_rate':
        async with app.state.container.database.sessions.begin() as session:
            await session.execute(delete(RateCard))
    else:
        key = await issue(
            admin,
            owner_app['id'],
            scopes=SCOPES,
            model_ids=[base['embed_model_id']],
            **({'currency': 'CNY'} if change == 'currency' else {'monthly_budget': '0'}),
        )
    file = await post(admin, key)
    response = await admin.post(
        '/api/v1/knowledge-bases/' + base['id'] + '/documents',
        headers=auth(key, uuid4()),
        json={'file_ids': [file.json()['id']]},
    )
    assert response.status_code == 202, response.text
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeJob)) == 1
        assert await session.scalar(select(func.count()).select_from(Invocation)) == 0


async def test_metadata_only_key_can_cancel_and_delete_with_source_unbound_and_model_disabled(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    owner_app, key, base = await setup(admin, provider, app)
    metadata = await issue(admin, owner_app['id'], scopes=['knowledge:read', 'knowledge:write'])
    file = await post(admin, key)
    endpoint = '/api/v1/knowledge-bases/' + base['id']
    operation = await admin.post(
        endpoint + '/documents', headers=auth(key, uuid4()), json={'file_ids': [file.json()['id']]}
    )
    assert operation.status_code == 202, operation.text
    c = app.state.container
    async with c.database.sessions.begin() as session:
        caller = await c.resource_access.bearer(session, key['api_key'], scope='files:read')
        owned = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
        await c.resource_access.unbind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'file',
            UUID(file.json()['id']),
            expected_version=owned.version,
        )
    cancelled = await admin.post('/api/v1/operations/' + operation.json()['id'] + '/cancel', headers=auth(metadata))
    assert cancelled.status_code == 202 and cancelled.json()['status'] == 'cancel_requested', cancelled.text
    envelope(await admin.get('/api/v1/files/' + file.json()['id'] + '/content', headers=auth(metadata)), 403)
    await c.knowledge_jobs.tick(UUID(operation.json()['native_id']))
    await admin.patch('/api/models/' + base['embed_model_id'], json={'enabled': False})
    current = await admin.get(endpoint, headers=auth(metadata))
    deleted = await admin.delete(
        endpoint + '?confirm_references=true',
        headers={**auth(metadata, uuid4()), 'If-Match': current.headers['etag']},
    )
    assert deleted.status_code == 202, deleted.text
    assert (await finish(admin, app, metadata, deleted.json()))['status'] == 'succeeded'
    async with c.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Invocation)) == 0
    assert provider.requests == []


@pytest.mark.usefixtures('real_vectors')
async def test_real_milvus_query_only_current_shared_source_acl_and_expired_snapshot(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from datetime import timedelta

    from zhiheng.modules.developer.orm import APIRequest
    from zhiheng.shared.time import utcnow

    real_store = app.state.container.knowledge_jobs.vectors
    _owner_app, key, base = await setup(admin, provider, app)
    c = app.state.container
    await set_legacy_policy(app, base['id'])
    c.knowledge_jobs.vectors = c.knowledge_preparation.vectors = real_store
    endpoint = '/api/v1/knowledge-bases/' + base['id']
    # A client can close immediately after durable admission; the native Worker continues.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        file = await post(client, key)
        operation = await client.post(
            endpoint + '/documents', headers=auth(key, uuid4()), json={'file_ids': [file.json()['id']]}
        )
        assert operation.status_code == 202, operation.text
    indexed = await finish(admin, app, key, operation.json())
    assert indexed['status'] == 'succeeded', str(indexed)
    readonly = await issue(admin, _owner_app['id'], scopes=['knowledge:read'])
    assert (await admin.get('/api/v1/operations/' + indexed['id'], headers=auth(readonly))).status_code == 200
    envelope(await admin.get('/api/v1/files/' + file.json()['id'] + '/content', headers=auth(readonly)), 403)
    async with member_client(app) as member:
        member_id = (await member.get('/api/auth/me')).json()['id']
        await legacy_grant(app, base['id'], member_id)
        assert (
            await admin.patch(
                '/api/files/' + file.json()['id'] + '/acl',
                json={
                    'expected_version': 1,
                    'access': 'private',
                    'entries': [{'user_id': member_id, 'effect': 'allow'}],
                },
            )
        ).status_code == 200
        member_app = await application(member)
        member_key = await issue(
            member,
            member_app['id'],
            scopes=['knowledge:query'],
            model_ids=await knowledge_models(admin, base['embed_model_id']),
        )
        async with c.database.sessions.begin() as session:
            principal = await c.identity.principal_for_user(session, UUID(member_id))
            await c.resource_access.bind(
                session,
                principal,
                UUID(member_app['id']),
                'knowledge_base',
                UUID(base['id']),
                ['read', 'query'],
                expected_version=1,
            )
        external = uuid4()
        result = await member.post(endpoint + '/query', headers=auth(member_key, external), json={'query': 'four'})
        assert result.status_code == 202, result.text
        queried = await finish(admin, app, member_key, result.json())
        assert queried['status'] == 'succeeded' and queried['result']['hits'][0]['content'] == 'four', str(queried)
        envelope(await member.get('/api/v1/files/' + file.json()['id'] + '/content', headers=auth(member_key)), 403)
        assert (await member.get('/api/v1/operations?limit=1', headers=auth(member_key))).json()['items'][0][
            'id'
        ] == queried['id']
        assert len(provider.requests) == 3
        assert (
            await admin.patch(
                '/api/files/' + file.json()['id'] + '/acl',
                json={'expected_version': 2, 'access': 'private', 'entries': []},
            )
        ).status_code == 200
        hidden = await member.get('/api/v1/operations/' + queried['id'], headers=auth(member_key))
        assert hidden.status_code == 200 and hidden.json()['result']['hits'] == [], hidden.text
        replay = await member.post(endpoint + '/query', headers=auth(member_key, external), json={'query': 'four'})
        assert replay.status_code == 202 and replay.json()['result']['hits'] == []
        async with c.database.sessions.begin() as session:
            request = await session.get(APIRequest, UUID(queried['id']))
            request.snapshot_expires_at = utcnow() - timedelta(hours=25)
        envelope(
            await member.post(endpoint + '/query', headers=auth(member_key, external), json={'query': 'four'}), 409
        )
        assert (await member.get('/api/v1/operations/' + queried['id'], headers=auth(member_key))).status_code == 200
        assert len(provider.requests) == 3


async def test_partial_result_and_cancel_preserve_native_projection(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    _owner_app, key, base = await setup(admin, provider, app)
    endpoint = '/api/v1/knowledge-bases/' + base['id']
    good = await post(admin, key)
    bad = await post(
        admin, key, files={'file': ('broken.pdf', b'%PDF-1.7\ntruncated document', 'application/octet-stream')}
    )
    accepted = await admin.post(
        endpoint + '/documents', headers=auth(key, uuid4()), json={'file_ids': [good.json()['id'], bad.json()['id']]}
    )
    assert accepted.status_code == 202, accepted.text
    partial = await finish(admin, app, key, accepted.json())
    assert partial['status'] == 'partially_succeeded' and partial['progress']['failed'] == 1, str(partial)
    assert partial['result']['status'] == 'partial'
    failed_document = next(row for row in partial['result']['document_results'] if row['status'] == 'failed')
    assert failed_document['error']['code'] == 'invalid_document'
    stopped = await admin.post('/api/v1/operations/' + partial['id'] + '/cancel', headers=auth(key))
    assert stopped.status_code == 202 and stopped.json()['status'] == 'partially_succeeded'


async def publish_claim(c: Container, native: UUID) -> BuildClaim:
    for _ in range(20):
        await c.knowledge_jobs.tick(native)
        async with c.database.sessions() as session:
            children = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
            job = await session.get(KnowledgeJob, native)
            ready = job.phase == 'publish'
        for child in children:
            await execute_run(c, child)
        if ready:
            break
    assert ready
    async with c.database.sessions.begin() as session:
        claim = await c.knowledge_jobs.store.claim(session, native)
    assert claim is not None
    return claim


@pytest.mark.parametrize('phase', ['claim', 'publish'])
async def test_native_build_locks_kb_before_job_under_real_pg_contention(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, phase: str
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from zhiheng.modules.knowledge.orm import KnowledgeBase

    _owner_app, key, base = await setup(admin, provider, app)
    file = await post(admin, key)
    operation = await admin.post(
        '/api/v1/knowledge-bases/' + base['id'] + '/documents',
        headers=auth(key, uuid4()),
        json={'file_ids': [file.json()['id']]},
    )
    assert operation.status_code == 202, operation.text
    c = app.state.container
    native = UUID(operation.json()['native_id'])
    claim = await publish_claim(c, native) if phase == 'publish' else None
    entered = asyncio.Event()
    acquired = asyncio.Event()
    release = asyncio.Event()
    backend_pid: list[int] = []

    async def work() -> None:
        async with c.database.sessions.begin() as session:
            backend_pid.append(await session.scalar(text('SELECT pg_backend_pid()')))
            entered.set()
            if phase == 'claim':
                await c.knowledge_jobs.store.claim(session, native)
            else:
                await c.knowledge_jobs.store.publish(session, claim, claim.binding)
            acquired.set()
            await release.wait()

    unlocked = False
    async with c.database.sessions.begin() as holder:
        await holder.execute(select(KnowledgeBase).where(KnowledgeBase.id == UUID(base['id'])).with_for_update())
        task = asyncio.create_task(work())
        try:
            await asyncio.wait_for(entered.wait(), 2)
            for _ in range(100):
                async with c.database.sessions() as observer:
                    waiting = await observer.scalar(
                        text('SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid'), {'pid': backend_pid[0]}
                    )
                if waiting == 'Lock' or acquired.is_set():
                    break
                await asyncio.sleep(0.01)
            async with c.database.sessions.begin() as checker:
                try:
                    await checker.execute(
                        select(KnowledgeJob).where(KnowledgeJob.id == native).with_for_update(nowait=True)
                    )
                    unlocked = True
                except DBAPIError as error:
                    assert error.orig.sqlstate == '55P03'
        finally:
            release.set()
    await asyncio.wait_for(task, 5)
    assert unlocked, 'Worker held Job while waiting for a previously locked KB'


async def test_original_source_acl_revoked_while_waiting_for_model_stops_provider(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_app, key, base = await setup(admin, provider, app)
    c = app.state.container
    await set_legacy_policy(app, base['id'])
    actor = (await admin.get('/api/auth/me')).json()['id']
    async with member_client(app) as owner:
        uploaded = await owner.post(
            '/api/files?filename=shared.txt',
            content=b'Original shared source',
            headers={'Content-Type': 'application/octet-stream'},
        )
        assert uploaded.status_code == 201, uploaded.text
        file = uploaded.json()
        assert (
            await owner.patch(
                '/api/files/' + file['id'] + '/acl',
                json={'expected_version': 1, 'access': 'private', 'entries': [{'user_id': actor, 'effect': 'allow'}]},
            )
        ).status_code == 200
        async with c.database.sessions.begin() as session:
            caller = await c.resource_access.bearer(session, key['api_key'], scope='files:read')
            owned = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
            await c.resource_access.bind(
                session,
                caller.principal,
                UUID(owner_app['id']),
                'file',
                UUID(file['id']),
                ['read'],
                expected_version=owned.version,
            )
        accepted = await admin.post(
            '/api/v1/knowledge-bases/' + base['id'] + '/documents',
            headers=auth(key, uuid4()),
            json={'file_ids': [file['id']]},
        )
        assert accepted.status_code == 202, accepted.text
        for _ in range(3):
            await c.knowledge_jobs.tick(UUID(accepted.json()['native_id']))
        async with c.database.sessions() as session:
            child = await session.scalar(select(Run.id).where(Run.status == 'pending'))
        assert child is not None
        original = c.models.resolve
        revoked = False

        async def resolve(session: AsyncSession, model_id: UUID, **kwargs: bool) -> ModelBinding:
            nonlocal revoked
            binding = await original(session, model_id, **kwargs)
            if not revoked:
                revoked = True
                changed = await owner.patch(
                    '/api/files/' + file['id'] + '/acl',
                    json={'expected_version': 2, 'access': 'private', 'entries': []},
                )
                assert changed.status_code == 200, changed.text
            return binding

        monkeypatch.setattr(c.models, 'resolve', resolve)
        await execute_run(c, child)
        assert revoked
        assert provider.requests == [], 'A Key administrator bypassed a revoked actual source ACL after the model wait'
        result = await finish(admin, app, key, accepted.json())
        assert result['status'] == 'failed' and result['result']['document_results'] == [], str(result)


async def test_cancel_own_shared_query_needs_write_scope_and_current_query_grant(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    _owner_app, key, base = await setup(admin, provider, app)
    c = app.state.container
    endpoint = '/api/v1/knowledge-bases/' + base['id']
    file = await post(admin, key)
    indexed = await admin.post(
        endpoint + '/documents', headers=auth(key, uuid4()), json={'file_ids': [file.json()['id']]}
    )
    assert indexed.status_code == 202, indexed.text
    assert (await finish(admin, app, key, indexed.json()))['status'] == 'succeeded'
    async with member_client(app) as member:
        member_id = (await member.get('/api/auth/me')).json()['id']
        sharing_path = '/api/knowledge/bases/' + base['id']
        acl = (await admin.get(sharing_path + '/acl')).json()
        invitation = await admin.post(
            sharing_path + '/invitations',
            json={'expected_acl_version': acl['base']['acl_version'], 'client_request_id': str(uuid4())},
        )
        assert invitation.status_code == 201, invitation.text
        accepted = await member.post('/api/knowledge/invitations/accept', json={'token': invitation.json()['token']})
        assert accepted.status_code == 200, accepted.text
        ranker = (await admin.get('/api/knowledge/settings')).json()['default_rerank_model_id']
        member_app = await application(member)
        query_key = await issue(
            member, member_app['id'], scopes=['knowledge:query'], model_ids=[base['embed_model_id'], ranker]
        )
        cancellation_key = await issue(member, member_app['id'], scopes=['knowledge:write'])
        async with c.database.sessions.begin() as session:
            principal = await c.identity.principal_for_user(session, UUID(member_id))
            await c.resource_access.bind(
                session,
                principal,
                UUID(member_app['id']),
                'knowledge_base',
                UUID(base['id']),
                ['query'],
                expected_version=1,
            )
        queried = await member.post(endpoint + '/query', headers=auth(query_key, uuid4()), json={'query': 'query'})
        assert queried.status_code == 202, queried.text
        cancel_url = '/api/v1/operations/' + queried.json()['id'] + '/cancel'
        envelope(await member.post(cancel_url, headers=auth(query_key)), 403)
        cancelled = await member.post(cancel_url, headers=auth(cancellation_key))
        assert cancelled.status_code == 202 and cancelled.json()['status'] == 'cancel_requested', cancelled.text
        assert cancelled.json()['result']['hits'] == []
        await c.knowledge_preparation.tick(UUID(queried.json()['native_id']))
        assert (await member.get('/api/v1/operations/' + queried.json()['id'], headers=auth(query_key))).json()[
            'status'
        ] == 'cancelled'
    assert len(provider.requests) == 1


async def test_cancelled_parent_projects_cancel_requested_until_native_child_stops(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from zhiheng.runtime.contracts import RunUsage

    _owner_app, key, base = await setup(admin, provider, app)
    c = app.state.container
    file = await post(admin, key)
    accepted = await admin.post(
        '/api/v1/knowledge-bases/' + base['id'] + '/documents',
        headers=auth(key, uuid4()),
        json={'file_ids': [file.json()['id']]},
    )
    assert accepted.status_code == 202, accepted.text
    for _ in range(3):
        await c.knowledge_jobs.tick(UUID(accepted.json()['native_id']))
    async with c.database.sessions.begin() as session:
        child = await session.scalar(select(Run.id).where(Run.status == 'pending'))
        claim = await c.runs.claim(session, child, 30)
    assert claim is not None
    endpoint = '/api/v1/operations/' + accepted.json()['id']
    assert (await admin.post(endpoint + '/cancel', headers=auth(key))).json()['status'] == 'cancel_requested'
    await c.knowledge_jobs.tick(UUID(accepted.json()['native_id']))
    async with c.database.sessions() as session:
        assert (await session.get(KnowledgeJob, UUID(accepted.json()['native_id']))).status == 'cancelled'
        assert (await session.get(Run, child)).status == 'cancel_requested'
    waiting = await admin.get(endpoint, headers=auth(key))
    assert waiting.status_code == 200 and waiting.json()['status'] == 'cancel_requested', waiting.text
    async with c.database.sessions.begin() as session:
        await c.runs.finish(
            session,
            child,
            claim.attempt_id,
            status='cancelled',
            content='',
            usage=RunUsage(input_tokens=0, output_tokens=0, status='known'),
        )
        await c.usage.settle(session, child, rejected_before_execution=True)
    assert (await admin.get(endpoint, headers=auth(key))).json()['status'] == 'cancelled'
    assert provider.requests == []


async def test_admin_query_implicit_foreign_source_checks_current_acl_and_filters_completed_hits(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    from datetime import timedelta

    from zhiheng.modules.files.orm import FileGrant
    from zhiheng.shared.time import utcnow

    owner_app, key, base = await setup(admin, provider, app)
    c = app.state.container
    await set_legacy_policy(app, base['id'])
    actor = (await admin.get('/api/auth/me')).json()['id']
    async with member_client(app) as owner:
        uploaded = await owner.post(
            '/api/files?filename=foreign.txt',
            content=b'Foreign private source',
            headers={'Content-Type': 'application/octet-stream'},
        )
        assert uploaded.status_code == 201, uploaded.text
        file = uploaded.json()
        assert (
            await owner.patch(
                '/api/files/' + file['id'] + '/acl',
                json={
                    'expected_version': 1,
                    'access': 'private',
                    'entries': [
                        {'user_id': actor, 'effect': 'allow', 'expires_at': (utcnow() + timedelta(hours=1)).isoformat()}
                    ],
                },
            )
        ).status_code == 200
        async with c.database.sessions.begin() as session:
            caller = await c.resource_access.bearer(session, key['api_key'], scope='files:read')
            owned = await c.applications.require_owned(session, caller.principal.id, UUID(owner_app['id']))
            await c.resource_access.bind(
                session,
                caller.principal,
                UUID(owner_app['id']),
                'file',
                UUID(file['id']),
                ['read'],
                expected_version=owned.version,
            )
        endpoint = '/api/v1/knowledge-bases/' + base['id']
        admitted = await admin.post(
            endpoint + '/documents', headers=auth(key, uuid4()), json={'file_ids': [file['id']]}
        )
        assert admitted.status_code == 202, admitted.text
        assert (await finish(admin, app, key, admitted.json()))['status'] == 'succeeded'
        ranker = (await admin.get('/api/knowledge/settings')).json()['default_rerank_model_id']
        query_only = await issue(
            admin, owner_app['id'], scopes=['knowledge:query'], model_ids=[base['embed_model_id'], ranker]
        )
        query = await admin.post(endpoint + '/query', headers=auth(query_only, uuid4()), json={'query': 'private'})
        assert query.status_code == 202, query.text
        completed = await finish(admin, app, query_only, query.json())
        assert completed['status'] == 'succeeded' and completed['result']['hits'], str(completed)
        queued = await admin.post(endpoint + '/query', headers=auth(query_only, uuid4()), json={'query': 'again'})
        assert queued.status_code == 202, queued.text
        async with c.database.sessions.begin() as session:
            # Expiry changes current access without a File version change, as wall-clock expiry does.
            grant = await session.get(FileGrant, (UUID(file['id']), UUID(actor)))
            assert grant is not None
            grant.expires_at = utcnow() - timedelta(seconds=1)
        filtered = await admin.get('/api/v1/operations/' + completed['id'], headers=auth(query_only))
        assert filtered.status_code == 200 and filtered.json()['result']['hits'] == [], filtered.text
        failed = await finish(admin, app, query_only, queued.json())
        assert failed['status'] == 'failed' and failed['result']['hits'] == [], str(failed)
        assert len(provider.requests) == 3
