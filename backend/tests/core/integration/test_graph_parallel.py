import asyncio
import os
import secrets
from collections import Counter
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.engine import make_url

from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_knowledge_failures import finish_job, source
from tests.core.integration.test_knowledge_milvus import real_vectors  # noqa: F401
from tests.core.provider import ProviderServer
from tests.core.resources import resource
from zhiheng.modules.developer.orm import Invocation
from zhiheng.modules.knowledge.adapters.lightrag.vector import GraphVectorManifest
from zhiheng.modules.knowledge.graph.orm import GraphAttempt, GraphGeneration, GraphStep
from zhiheng.runtime.orm import Run
from zhiheng.worker.main import build_worker


async def test_parallel_four_managed_chunks_summary_and_single_slot_settle_exactly_once(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, real_vectors: object
) -> None:
    from pymilvus import MilvusClient

    container = app.state.container
    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus required')
    provider.graph_parallel = True
    embed = await embedding_model(admin, provider)
    response = await admin.post('/api/knowledge/bases', json={'name': 'Parallel graph', 'embed_model_id': embed})
    assert response.status_code == 201, response.text
    base = response.json()
    content = '\n'.join(
        f'审查样本{i}。甲机构和乙公司开展合作研究。' + ('独立材料' + str(i)) * 18 for i in range(4)
    ).encode()
    files = [await source(admin, f'parallel-{i}.txt', content) for i in range(2)]
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={
            'file_ids': [item['id'] for item in files],
            'client_request_id': str(uuid4()),
            'chunk_size': 100,
            'chunk_overlap': 0,
        },
    )
    assert response.status_code == 202, response.text
    assert (await finish_job(admin, app, response.json()))['status'] == 'completed'
    response = await admin.post(
        '/api/models/connections',
        json={'name': 'Parallel graph', 'base_url': f'http://127.0.0.1:{provider.server_port}/v1', 'api_key': 'local'},
    )
    assert response.status_code == 201, response.text
    response = await admin.post(
        '/api/models',
        json={
            'name': 'Parallel graph',
            'connection_id': response.json()['id'],
            'upstream_model': 'graph',
            'max_output_tokens': 4096,
        },
    )
    assert response.status_code == 201, response.text
    chat = response.json()['id']
    for model in (embed, chat):
        assert (
            await admin.post(
                f'/api/models/{model}/rate-cards',
                json={
                    'billing_mode': 'token',
                    'input_price': '0.000001',
                    'output_price': '0.000002',
                },
            )
        ).status_code == 201
    endpoint = f'/api/knowledge/bases/{base["id"]}/graph'
    configured = await admin.patch(
        endpoint + '/config',
        json={
            'expected_version': 0,
            'llm_model_id': chat,
            'max_output_tokens': 2048,
            'concurrency': 4,
            'timeout_seconds': 240,
        },
    )
    assert configured.status_code == 200, configured.text
    url = make_url(container.settings.database_url.get_secret_value().replace('postgresql+asyncpg:', 'postgresql:'))
    pg = await asyncpg.connect(url.render_as_string(hide_password=False))
    role, password = 'graph_parallel_' + uuid4().hex, secrets.token_urlsafe(24)
    client = MilvusClient(uri=uri, timeout=8)
    collections = {}
    worker = worker_task = None
    resource('intent', 'postgres_role', role, database=url.database)
    await pg.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\'')
    resource('created', 'postgres_role', role, database=url.database)
    try:
        await pg.execute(f'REVOKE CREATE,TEMPORARY ON DATABASE "{url.database}" FROM PUBLIC')
        await pg.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        await pg.execute(f'GRANT USAGE ON SCHEMA knowledge_graph_sdk TO "{role}"')
        await pg.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA knowledge_graph_sdk TO "{role}"')
        container.graph_sdk.database_url = url.set(username=role, password=password).render_as_string(
            hide_password=False
        )
        container.graph_sdk.uri = uri
        worker = build_worker(container, max_jobs=1)
        worker_task = asyncio.create_task(worker.async_run())
        for _ in range(3):
            before = len(provider.requests)
            response = await admin.post(
                endpoint + '/build',
                json={
                    'client_request_id': str(uuid4()),
                    'expected_version': 1,
                    'rebuild': True,
                },
            )
            assert response.status_code == 202, response.text
            job = response.json()
            async with container.database.sessions() as session:
                attempt = await session.get(GraphAttempt, UUID(job['attempt_id']))
                generation = await session.get(GraphGeneration, UUID(job['graph_revision']))
                manifest = GraphVectorManifest(
                    UUID(base['id']), generation.id, attempt.id, generation.fingerprint, 2, attempt.workspace
                )
            for vector_role in ('entities', 'relationships', 'chunks'):
                name, description = manifest.collection(vector_role), manifest.description(vector_role)
                collections[name] = description
                resource('intent', 'milvus_collection', name, description=description, database='default')
            for _ in range(2400):
                response = await admin.get(endpoint + '/jobs/' + job['id'])
                assert response.status_code == 200, response.text
                job = response.json()
                if job['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
                    break
                await asyncio.sleep(0.05)
            assert job['status'] == 'completed', job
            assert job['progress']['total_chunks'] == 10
            requests = provider.requests[before:]
            stages = Counter()
            for request in requests:
                if request['model'] != 'graph':
                    stages['embedding'] += 1
                    continue
                prompt = '\n'.join(message['content'] for message in request['messages'])
                stages[
                    'summary'
                    if 'synthesize a list of descriptions' in prompt
                    else 'glean'
                    if len(request['messages']) > 2
                    else 'extract'
                ] += 1
            assert stages['extract'] == stages['glean'] == 10, stages
            assert stages['summary'] > 0, stages
            async with container.database.sessions() as session:
                runs = list(await session.scalars(select(Run).where(Run.resource_id == UUID(job['attempt_id']))))
                steps = list(
                    await session.scalars(select(GraphStep).where(GraphStep.attempt_id == UUID(job['attempt_id'])))
                )
                invocations = list(
                    await session.scalars(select(Invocation).where(Invocation.run_id.in_([r.id for r in runs])))
                )
            assert len(requests) == len(runs) == len(steps) == len(invocations)
            assert {row.run_id for row in steps} == {row.run_id for row in invocations} == {row.id for row in runs}
            assert all(row.status == 'completed' and row.attempt_no == 1 and row.provider_started for row in runs)
            assert all(row.status == 'known' and row.billable_cost is not None for row in invocations)
            assert all(
                (row.input_tokens, row.output_tokens) == ((6, 0) if row.operation == 'embedding' else (8, 2))
                for row in invocations
            )
    finally:
        provider.release.set()
        if worker_task is not None:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        if worker is not None:
            await worker.close()
        await container.graph_sdk.close()
        for name, description in collections.items():
            if client.has_collection(name):
                assert client.describe_collection(name)['description'] == description
                client.drop_collection(name)
            assert not client.has_collection(name)
            resource('deleted', 'milvus_collection', name, database='default')
        client.close()
        await pg.execute(f'DROP OWNED BY "{role}"')
        await pg.execute(f'DROP ROLE "{role}"')
        resource('deleted', 'postgres_role', role, database=url.database)
        await pg.close()
