"""Lifecycle gates use private fixtures and a local mock model provider only."""

from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration import test_knowledge_invitations
from tests.core.integration.test_agents import config
from tests.core.integration.test_knowledge import VectorFixture, embedding_model
from tests.core.integration.test_knowledge_failures import finish_job, ready_base
from tests.core.integration.test_knowledge_invitations import SharingFixture, invite
from tests.core.integration.test_knowledge_required_rerank import finish_query
from tests.core.provider import ProviderServer
from zhiheng.modules.agents.orm import Agent
from zhiheng.modules.knowledge.orm import KnowledgeDocument, KnowledgeGrant, RetrievalIntent
from zhiheng.modules.knowledge.public import Hit
from zhiheng.modules.models.public import ModelMessage, ModelRequest
from zhiheng.modules.work.orm import WorkTask
from zhiheng.runtime.contracts import ClaimedRun
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

sharing = test_knowledge_invitations.sharing


@pytest.mark.parametrize('deleted', [False, True])
async def test_shared_owner_lifecycle_blocks_all_content_and_preserves_relationships(  # noqa: PLR0915 -- one lifecycle must preserve the original grants and source data across all entry checks
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    sharing: SharingFixture,
    deleted: bool,
    model_id: str,
) -> None:
    container = app.state.container
    container.knowledge_jobs.vectors = container.knowledge_preparation.vectors = VectorFixture()
    invitation = await invite(sharing)
    accepted = await sharing.first.post('/api/knowledge/invitations/accept', json={'token': invitation['token']})
    assert accepted.status_code == 200
    path, base_id = sharing.path, UUID(sharing.base['id'])
    upload = await sharing.owner.post(
        path + '/uploads',
        params={'filename': 'protected.txt'},
        content=b'SECRET batch one source',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert upload.status_code == 201, upload.text
    job = await sharing.owner.post(
        path + '/documents',
        json={
            'file_ids': [upload.json()['file']['id']],
            'client_request_id': str(uuid4()),
        },
    )
    assert job.status_code == 202, job.text
    assert (await finish_job(sharing.owner, app, job.json()))['status'] == 'completed'
    document = (await sharing.first.get(path + '/documents')).json()['items'][0]
    raw_path = '/api/files/' + document['file_id']
    document_path = path + '/documents/' + document['id']
    assert (await sharing.first.get(document_path + '/preview')).status_code == 200
    assert (await sharing.first.get(raw_path + '/download')).content == b'SECRET batch one source'
    async with container.database.sessions.begin() as session:
        artifact = await container.files.create_artifact(
            session,
            source_id=UUID(document['file_id']),
            content=b'{"text":"SECRET derivative"}',
            storage_key='fixture/batch1-derived',
            parser_version='batch1-fixture',
        )
    artifact_path = '/api/files/' + str(artifact.id)
    assert (await sharing.first.get(artifact_path)).status_code == 200
    healthy = await sharing.first.post(
        '/api/knowledge/bases',
        json={
            'name': 'Healthy personal library',
            'embed_model_id': sharing.base['embed_model_id'],
        },
    )
    assert healthy.status_code == 201, healthy.text
    query_body = {'query': 'batch source', 'client_request_id': str(uuid4())}
    query = await sharing.first.post(path + '/query', json=query_body)
    assert query.status_code == 202, query.text
    result = await finish_query(sharing.first, app, query.json())
    assert result['status'] == 'completed' and result['hits'], result
    reader_id = UUID(sharing.first_id)
    owner_id = UUID(sharing.base['owner_id'])
    async with container.database.sessions() as session:
        principal = await container.identity.principal_for_user(session, reader_id)
        snapshot = await container.knowledge_preparation.store.snapshot(session, principal, [base_id])
        assert await container.knowledge_preparation.visible_hits(
            session, principal, [Hit.model_validate(h) for h in result['hits']]
        )

    # Reproduce the interval after retrieval admission but before provider execution.
    claims = []
    async with container.database.sessions.begin() as session:
        binding = await container.models.resolve(session, UUID(model_id))
        agent = Agent(owner_id=reader_id, config=config(model_id))
        session.add(agent)
        await session.flush()
        task = WorkTask(
            owner_id=reader_id,
            title='Owner gate',
            goal='Owner gate',
            create_active_agent_id=agent.id,
            create_client_request_id=uuid4(),
            create_fingerprint='owner-gate',
        )
        session.add(task)
        await session.flush()
        for kind in ('chat', 'agent_preview', 'agent_invocation', 'work_turn'):
            request = ModelRequest(binding=binding, messages=(ModelMessage('user', 'Frozen protected source context'),))
            run = Run(
                owner_id=reader_id,
                billing_owner_id=reader_id,
                resource_type=kind,
                resource_id=task.id if kind == 'work_turn' else agent.id if kind.startswith('agent_') else uuid4(),
                client_request_id=uuid4(),
                fingerprint='admitted-owner-gate',
                payload=request.model_dump(mode='json'),
            )
            session.add(run)
            await session.flush()
            session.add(
                RetrievalIntent(
                    namespace_id=reader_id,
                    client_request_id=uuid4(),
                    actor_id=reader_id,
                    owner_id=reader_id,
                    resource_type=kind,
                    resource_id=run.resource_id,
                    fingerprint='admitted-owner-gate',
                    request={},
                    dependencies={'bases': [base.model_dump(mode='json') for base in snapshot]},
                    status='admitted',
                    main_run_id=run.id,
                    deadline=utcnow() - timedelta(minutes=1),
                )
            )
            claims.append(ClaimedRun(run.id, kind, run.resource_id, reader_id, uuid4(), run.payload, reader_id))

    account = (await admin.get('/api/admin/users/' + str(owner_id))).json()
    if deleted:
        changed = await admin.request(
            'DELETE',
            '/api/admin/users/' + str(owner_id),
            params={'expected_version': account['version']},
            headers={'Content-Type': 'application/json'},
        )
    else:
        changed = await admin.patch(
            '/api/admin/users/' + str(owner_id), json={'expected_version': account['version'], 'is_active': False}
        )
    assert changed.status_code in (200, 204), changed.text
    calls = len(provider.requests)
    expected = 'owner_deleted' if deleted else 'owner_disabled'
    for scope in ('all', 'shared'):
        response = await sharing.first.get('/api/knowledge/bases', params={'scope': scope})
        assert response.status_code == 200, response.text
        row = next(row for row in response.json() if row['id'] == str(base_id))
        assert row['availability'] == expected
        assert set(row) == {'id', 'name', 'owner_id', 'owner_name', 'availability'}
        assert 'SECRET' not in response.text
    assert len((await sharing.first.get('/api/knowledge/bases?scope=owned')).json()) == 1
    assert (await sharing.first.get('/api/auth/me')).status_code == 200
    assert (await sharing.first.get('/api/knowledge/available')).json() == []
    assert not any(row['id'] == str(base_id) for row in (await admin.get('/api/knowledge/bases')).json())
    assert not any(row['id'] == str(base_id) for row in (await sharing.second.get('/api/knowledge/bases')).json())
    for client in (sharing.first, admin):
        for endpoint in (
            path,
            path + '/documents',
            path + '/folders',
            path + '/jobs',
            path + '/graph/config',
            document_path + '/preview',
            document_path + '/download',
            artifact_path,
            artifact_path + '/download',
            raw_path,
            raw_path + '/download',
            raw_path + '/content',
        ):
            response = await client.get(endpoint)
            assert response.status_code == 404, (endpoint, response.status_code, response.text)
            assert 'SECRET' not in response.text
    for body in (query_body, {**query_body, 'client_request_id': str(uuid4())}):
        response = await sharing.first.post(path + '/query', json=body)
        assert response.status_code == 404 and response.json()['error']['code'] == 'knowledge_owner_unavailable', (
            response.text
        )
    assert (await sharing.first.get('/api/knowledge/preparations/' + result['id'])).status_code == 404
    async with container.database.sessions() as session:
        principal = await container.identity.principal_for_user(session, reader_id)
        # Chat, Agent, Work and resource API use these same domain and frozen-source gates.
        assert (
            await container.knowledge_preparation.visible_hits(
                session, principal, [Hit.model_validate(h) for h in result['hits']]
            )
            == []
        )
        assert await container.knowledge.delegable_metadata(session, principal)  # healthy library still present
        with pytest.raises(AppError, match='创建者已'):
            await container.knowledge_preparation.store.snapshot(session, principal, [base_id])
        with pytest.raises(AppError, match='创建者已'):
            await container.knowledge_preparation.store.revalidate(session, principal, snapshot)
        with pytest.raises(AppError, match='创建者已'):
            await container.resource_access.domain_actions(session, principal, 'knowledge_base', base_id, lock=False)
        assert await session.get(KnowledgeGrant, (base_id, reader_id)) is not None
        assert (
            await session.scalar(
                select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.base_id == base_id)
            )
            == 1
        )
    for claim in claims:
        with pytest.raises(AppError, match='创建者已'):
            await container.execution_authorization.require(claim)
    assert len(provider.requests) == calls
    if not deleted:
        account = (await admin.get('/api/admin/users/' + str(owner_id))).json()
        enabled = await admin.patch(
            '/api/admin/users/' + str(owner_id), json={'expected_version': account['version'], 'is_active': True}
        )
        assert enabled.status_code == 200, enabled.text
        restored = await sharing.first.get(path)
        assert restored.status_code == 200 and restored.json()['availability'] == 'available'
        assert (await sharing.first.get(document_path + '/download')).content == b'SECRET batch one source'
        assert len((await sharing.first.get('/api/knowledge/available')).json()) == 1
        async with container.database.sessions.begin() as session:
            for claim in claims:
                await container.knowledge_preparation.require_run_bases(session, claim.id, claim.resource_id)


@pytest.mark.parametrize(
    'field,model_type', [('default_embed_model_id', 'embedding'), ('default_rerank_model_id', 'rerank')]
)
async def test_default_models_cannot_be_deleted_until_replaced(
    admin: httpx.AsyncClient, provider: ProviderServer, field: str, model_type: str
) -> None:
    embed = await embedding_model(admin, provider)
    current = (await admin.get('/api/knowledge/settings')).json()
    if field == 'default_embed_model_id':
        configured = await admin.patch(
            '/api/knowledge/settings', json={'expected_version': current['version'], field: embed}
        )
        assert configured.status_code == 200
        current = configured.json()
    old = current[field]
    models = (await admin.get('/api/models')).json()
    connection = next(m['connection_id'] for m in models if m['id'] == old)
    stopped = await admin.patch('/api/models/' + old, json={'enabled': False})
    assert stopped.status_code == 200, stopped.text
    denied = await admin.request('DELETE', '/api/models/' + old, json={})
    assert denied.status_code == 409, denied.text
    refs = denied.json()['error']['details']['references']
    assert any(ref['kind'] == 'knowledge_defaults' and ref['path'] == '/knowledge/settings' for ref in refs)
    assert (await admin.get('/api/knowledge/settings')).json()[field] == old
    replacement = await admin.post(
        '/api/models',
        json={
            'name': 'Replacement',
            'connection_id': connection,
            'upstream_model': 'replacement',
            'model_type': model_type,
        },
    )
    assert replacement.status_code == 201, replacement.text
    saved = await admin.patch(
        '/api/knowledge/settings', json={'expected_version': current['version'], field: replacement.json()['id']}
    )
    assert saved.status_code == 200, saved.text
    assert (await admin.request('DELETE', '/api/models/' + old, json={})).status_code == 200
    assert not provider.requests


async def test_frozen_reranker_stays_referenced_after_default_switch_until_parent_finishes(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    path = '/api/knowledge/bases/' + base['id']
    queued = await admin.post(path + '/query', json={'query': 'service', 'client_request_id': str(uuid4())})
    assert queued.status_code == 202, queued.text
    defaults = (await admin.get('/api/knowledge/settings')).json()
    old = defaults['default_rerank_model_id']
    original = next(m for m in (await admin.get('/api/models')).json() if m['id'] == old)
    replacement = await admin.post(
        '/api/models',
        json={
            'name': 'New default',
            'connection_id': original['connection_id'],
            'upstream_model': 'replacement-rerank',
            'model_type': 'rerank',
        },
    )
    assert replacement.status_code == 201
    assert (
        await admin.patch(
            '/api/knowledge/settings',
            json={'expected_version': defaults['version'], 'default_rerank_model_id': replacement.json()['id']},
        )
    ).status_code == 200
    denied = await admin.request('DELETE', '/api/models/' + old, json={})
    assert denied.status_code == 409, denied.text
    assert any(ref['id'] == queued.json()['id'] for ref in denied.json()['error']['details']['references'])
    assert (await admin.patch('/api/models/' + old, json={'enabled': False})).status_code == 200
    document = (await admin.get(path + '/documents')).json()['items'][0]
    assert (await admin.get(path + '/documents/' + document['id'] + '/download')).status_code == 200
    async with app.state.container.database.sessions.begin() as session:
        intent = await session.get(RetrievalIntent, UUID(queued.json()['id']))
        intent.status = 'cancelled'
    assert (await admin.request('DELETE', '/api/models/' + old, json={})).status_code == 200


async def test_frozen_rerank_binding_protects_both_model_and_connection_without_child_run(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    container = app.state.container
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    async with container.database.sessions.begin() as session:
        binding = await container.models.resolve(session, UUID(model_id))
        intent = RetrievalIntent(
            namespace_id=owner_id,
            client_request_id=uuid4(),
            actor_id=owner_id,
            owner_id=owner_id,
            resource_type='knowledge_query',
            resource_id=uuid4(),
            fingerprint='frozen-ranker',
            request={},
            dependencies={'rerank_binding': binding.model_dump(mode='json')},
            deadline=utcnow() + timedelta(minutes=1),
        )
        session.add(intent)
        await session.flush()
        assert [ref.id for ref in await container.knowledge.model_references(session, binding.model_id)] == [intent.id]
        assert [ref.id for ref in await container.knowledge.connection_references(session, binding.connection_id)] == [
            intent.id
        ]
        intent.status = 'completed'
        await session.flush()
        assert not await container.knowledge.model_references(session, binding.model_id)
        assert not await container.knowledge.connection_references(session, binding.connection_id)
