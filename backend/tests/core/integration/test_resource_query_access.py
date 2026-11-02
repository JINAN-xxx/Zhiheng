"""A knowledge query capability may execute governed Embedding without bare inference."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_knowledge_failures import finish_job, source
from tests.core.integration.test_knowledge_milvus import real_vectors  # noqa: F401
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.provider import ProviderServer
from zhiheng.application.knowledge_preparation import PreparationStart
from zhiheng.bootstrap.container import Container
from zhiheng.modules.developer.orm import Invocation
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.worker.execution import execute_run


async def assert_unassigned_embedding(
    admin: httpx.AsyncClient, c: Container, application_id: str, base_id: str
) -> None:
    empty = await issue(admin, application_id, scopes=['knowledge:query'])
    async with c.database.sessions.begin() as session:
        missing = await c.resource_access.bearer(session, empty['api_key'], scope='knowledge:query')
        with pytest.raises(AppError) as rejected:
            await c.knowledge_preparation.start(
                session,
                principal=missing.principal,
                identity=missing.identity,
                body=PreparationStart(
                    owner_id=missing.principal.id,
                    resource_type='knowledge_query',
                    resource_id=UUID(base_id),
                    request_id=uuid4(),
                    namespace_id=UUID(application_id),
                    value='missing-model',
                    request={'query': 'Which port?'},
                    base_ids=[UUID(base_id)],
                    dependencies={},
                ),
            )
        assert rejected.value.status_code == 404
        assert list(await session.scalars(select(Invocation).where(Invocation.key_id == UUID(empty['id'])))) == []


@pytest.mark.usefixtures('real_vectors')
async def test_query_only_key_runs_original_metered_embedding_with_real_vectors(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    c = app.state.container
    model = await embedding_model(admin, provider)
    response = await admin.post('/api/knowledge/bases', json={'name': 'Resource query', 'embed_model_id': model})
    assert response.status_code == 201, response.text
    base = response.json()
    file = await source(admin, 'resource-query.txt', b'The service listens on port 8080.')
    job = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={'file_ids': [file['id']], 'client_request_id': str(uuid4())},
    )
    assert job.status_code == 202, job.text
    finished = await finish_job(admin, app, job.json())
    assert finished['status'] == 'completed', finished
    rate = await admin.post(
        f'/api/models/{model}/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.000001', 'output_price': '0'},
    )
    assert rate.status_code == 201, rate.text
    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=['knowledge:query'], model_ids=[model])
    external = uuid4()
    async with c.database.sessions.begin() as session:
        caller = await c.resource_access.bearer(session, key['api_key'], scope='knowledge:query')
        await c.resource_access.bind(
            session,
            caller.principal,
            UUID(owner_app['id']),
            'knowledge_base',
            UUID(base['id']),
            ['query'],
            expected_version=1,
        )
    await assert_unassigned_embedding(admin, c, owner_app['id'], base['id'])
    async with c.database.sessions.begin() as session:
        # Preflight holds Key/App but leaves KB available until Model/Connection have been locked.
        await c.resource_access.require(session, caller, 'knowledge_base', UUID(base['id']), 'query', lock=False)
        async with c.database.sessions.begin() as other:
            await asyncio.wait_for(
                c.knowledge.authorize_base(other, caller.principal, UUID(base['id']), lock=True), timeout=2
            )
        await c.models.resolve_many(session, [UUID(model)])
        await c.resource_access.require(session, caller, 'knowledge_base', UUID(base['id']), 'query', lock=True)
        request = await c.resource_access.begin_request(
            session,
            caller,
            'knowledge.query',
            external,
            {'query': 'Which port?'},
            scope='knowledge:query',
            resources=({'kind': 'knowledge_base', 'id': base['id'], 'action': 'query'},),
        )
        prep = await c.knowledge_preparation.start(
            session,
            principal=caller.principal,
            identity=caller.identity,
            body=PreparationStart(
                owner_id=caller.principal.id,
                resource_type='knowledge_query',
                resource_id=UUID(base['id']),
                request_id=request.native_request_id,
                namespace_id=UUID(owner_app['id']),
                value='q' * 64,
                request={'query': 'Which port?'},
                base_ids=[UUID(base['id'])],
                dependencies={},
            ),
        )
        await c.api_requests.complete(
            session, request.id, result_kind='preparation', result_id=prep.id, snapshot={'id': str(prep.id)}
        )
    for _ in range(10):
        await c.knowledge_preparation.tick(prep.id)
        async with c.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(c, run_id)
        async with c.database.sessions() as session:
            row = await c.knowledge_preparation.store.get(session, prep.id)
        if row.status in {'completed', 'failed', 'cancelled', 'interrupted'}:
            break
    assert row.status == 'completed' and row.hits, row
    async with c.database.sessions.begin() as session:
        replay = await c.resource_access.begin_request(
            session,
            caller,
            'knowledge.query',
            external,
            {'query': 'Which port?'},
            scope='knowledge:query',
            resources=({'kind': 'knowledge_base', 'id': base['id'], 'action': 'query'},),
        )
        assert replay.id == request.id and replay.result_id == prep.id
        origin = await c.api_requests.origin(session, 'preparation', prep.id)
        assert origin.key_id == UUID(key['id']) and origin.application_id == UUID(owner_app['id'])
        invocations = list(await session.scalars(select(Invocation).where(Invocation.key_id == UUID(key['id']))))
        assert len(invocations) == 1 and invocations[0].operation == 'embedding'
        assert invocations[0].status == 'known' and invocations[0].charged_tokens > 0
    denied = await admin.post(
        '/v1/embeddings', headers={'Authorization': f'Bearer {key["api_key"]}'}, json={'model': model, 'input': 'hello'}
    )
    assert denied.status_code == 403, denied.text
