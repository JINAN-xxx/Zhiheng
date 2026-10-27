import asyncio
import os
import secrets
import hashlib
import json
from collections import Counter
from uuid import UUID, uuid4
from datetime import timedelta

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, func, event
from sqlalchemy.engine import make_url

from tests.core.integration.test_knowledge_failures import ready_base, source, finish_job
from tests.core.provider import ProviderServer
from tests.core.resources import resource
from zhiheng.modules.knowledge.graph.orm import GraphGeneration, GraphJob, GraphStep, GraphAttempt
from zhiheng.runtime.orm import Run, Outbox
from zhiheng.worker.main import build_worker
from zhiheng.shared.time import utcnow
from zhiheng.modules.knowledge.graph.orm import GraphPin
from zhiheng.modules.developer.orm import Invocation
from tests.core.integration.test_work import member_client


async def test_real_single_arq_slot_graph_runs_and_atomic_publish(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pymilvus import MilvusClient
    from arq.connections import ArqRedis

    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus required')
    container = app.state.container
    base = await ready_base(admin, app, provider)
    document = await source(admin, 'graph.txt', '甲机构和乙公司开展合作研究。'.encode())
    duplicate = await source(admin, 'same-text-different-file.txt', '甲机构和乙公司开展合作研究。'.encode())
    imported = await finish_job(
        admin,
        app,
        (
            await admin.post(
                f'/api/knowledge/bases/{base["id"]}/documents',
                json={'file_ids': [document['id'], duplicate['id']], 'client_request_id': str(uuid4())},
            )
        ).json(),
    )
    assert imported['status'] == 'completed', imported
    connection = (
        await admin.post(
            '/api/models/connections',
            json={'name': 'Graph', 'base_url': f'http://127.0.0.1:{provider.server_port}/v1', 'api_key': 'local'},
        )
    ).json()
    model = (
        await admin.post(
            '/api/models',
            json={
                'name': 'Graph',
                'connection_id': connection['id'],
                'upstream_model': 'graph',
                'max_output_tokens': 4096,
            },
        )
    ).json()
    rate = await admin.post(
        '/api/models/' + model['id'] + '/rate-cards',
        json={'billing_mode': 'token', 'input_price': '0.000001', 'output_price': '0.000001'},
    )
    assert rate.status_code == 201, rate.text
    url = make_url(container.settings.database_url.get_secret_value().replace('postgresql+asyncpg:', 'postgresql:'))
    pg = await asyncpg.connect(url.render_as_string(hide_password=False))
    role = 'graph_run_' + uuid4().hex
    password = secrets.token_urlsafe(24)
    runtime = url.set(username=role, password=password).render_as_string(hide_password=False)
    resource('intent', 'postgres_role', role, database=url.database)
    collections = {}
    keys = set()
    worker = None
    task = None
    milvus = MilvusClient(uri=uri, timeout=5)
    queue = container.settings.worker_queue
    for name in (queue, queue + ':heartbeat', queue + ':arq-health'):
        resource('intent', 'redis_key', name)
    original = ArqRedis.enqueue_job

    async def record_enqueue(self, *args, **kwargs):
        job_id = kwargs.get('_job_id')
        if job_id:
            for prefix in ('arq:job:', 'arq:result:', 'arq:in-progress:', 'arq:retry:'):
                key = prefix + job_id
                if key not in keys:
                    resource('intent', 'redis_key', key)
                    keys.add(key)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ArqRedis, 'enqueue_job', record_enqueue)
    try:
        await pg.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\'')
        resource('created', 'postgres_role', role, database=url.database)
        await pg.execute(f'REVOKE CREATE,TEMPORARY ON DATABASE "{url.database}" FROM PUBLIC')
        await pg.execute(f'GRANT USAGE ON SCHEMA knowledge_graph_sdk TO "{role}"')
        await pg.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA knowledge_graph_sdk TO "{role}"')
        container.graph_sdk.database_url = runtime
        container.graph_sdk.uri = uri
        endpoint = f'/api/knowledge/bases/{base["id"]}/graph'
        configured = await admin.patch(
            endpoint + '/config', json={'expected_version': 0, 'llm_model_id': model['id'], 'max_output_tokens': 4096}
        )
        assert configured.status_code == 200, configured.text
        request = {'expected_version': 1, 'client_request_id': str(uuid4())}
        admitted = await admin.post(endpoint + '/build', json=request)
        assert admitted.status_code == 202, admitted.text
        job = admitted.json()
        from zhiheng.modules.knowledge.adapters.lightrag.vector import GraphVectorManifest

        async with container.database.sessions() as session:
            attempt = await session.get(GraphAttempt, UUID(job['attempt_id']))
            generation = await session.get(GraphGeneration, UUID(job['graph_revision']))
            frozen = generation.frozen
            manifest = GraphVectorManifest(
                UUID(base['id']), generation.id, attempt.id, generation.fingerprint, 2, attempt.workspace
            )
        for vector_role in ('entities', 'relationships', 'chunks'):
            name = manifest.collection(vector_role)
            description = manifest.description(vector_role)
            assert not milvus.has_collection(name)
            collections[name] = description
            resource('intent', 'milvus_collection', name, description=description, database='default')
        before = len(provider.requests)
        worker = build_worker(container, burst=False, max_jobs=1)
        task = asyncio.create_task(worker.async_run())
        for _ in range(800):
            value = await admin.get(endpoint + '/jobs/' + job['id'])
            assert value.status_code == 200, value.text
            if value.json()['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
                job = value.json()
                break
            await asyncio.sleep(0.05)
        assert job['status'] == 'completed', job
        current_base = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
        opened = await admin.patch(
            f'/api/knowledge/bases/{base["id"]}/acl',
            json={'expected_version': current_base['acl_version'], 'access': 'public', 'entries': []},
        )
        assert opened.status_code == 200, opened.text
        documents = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items']
        for item in documents:
            info = (await admin.get('/api/files/' + item['file_id'])).json()
            grant = await admin.patch(
                '/api/files/' + item['file_id'] + '/acl',
                json={'expected_version': info['acl_version'], 'access': 'public', 'entries': []},
            )
            assert grant.status_code == 200, grant.text
        before_reuse_http = len(provider.requests)
        reused = await admin.post(
            endpoint + '/build', json={'expected_version': 1, 'client_request_id': str(uuid4()), 'rebuild': False}
        )
        assert reused.status_code == 202, reused.text
        assert (
            reused.json()['status'] == 'completed'
            and reused.json()['phase'] == 'reused'
            and reused.json()['graph_revision'] == job['graph_revision']
        )
        assert len(provider.requests) == before_reuse_http
        active_names = tuple(collections)

        def vector_hash():
            rows = {
                name: sorted(
                    milvus.query(
                        name,
                        filter='',
                        output_fields=[field['name'] for field in milvus.describe_collection(name)['fields']],
                        limit=1000,
                        consistency_level='Strong',
                    ),
                    key=lambda row: row['id'],
                )
                for name in active_names
            }
            return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()

        async def pg_hash(workspace=manifest.workspace):
            tables = await pg.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='knowledge_graph_sdk' ORDER BY tablename"
            )
            rows = {
                table['tablename']: [
                    dict(row)
                    for row in await pg.fetch(
                        f'SELECT * FROM knowledge_graph_sdk.{table["tablename"]} WHERE workspace=$1 ORDER BY 1,2',
                        workspace,
                    )
                ]
                for table in tables
            }
            return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()

        before_vector = vector_hash()
        before_pg = await pg_hash()
        before_query_http = len(provider.requests)
        labels = await admin.get(endpoint + '/labels')
        assert labels.status_code == 200, labels.text
        assert set(labels.json()['items']) == {'甲机构', '乙公司'}
        subgraph = await admin.get(endpoint + '/subgraph', params={'node_label': '*', 'max_nodes': 10, 'max_depth': 2})
        assert subgraph.status_code == 200, subgraph.text
        assert len(subgraph.json()['nodes']) == 2 and len(subgraph.json()['edges']) == 1
        query = await admin.post(
            endpoint + '/query',
            json={'client_request_id': str(uuid4()), 'query': '双方合作', 'mode': 'mix', 'top_k': 2},
        )
        assert query.status_code == 202, query.text
        query_job = query.json()
        for _ in range(600):
            polled = await admin.get(endpoint + '/jobs/' + query_job['id'])
            assert polled.status_code == 200, polled.text
            query_job = polled.json()
            if query_job['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
                break
            await asyncio.sleep(0.05)
        assert query_job['status'] == 'completed', query_job
        assert query_job['result']['context'] and query_job['result']['sources']
        assert len(query_job['result']['context'].encode()) <= 24000
        paid = provider.requests[before_query_http:]
        assert len(paid) == 2 and sum(call['model'] == 'graph' for call in paid) == 1
        assert next(call for call in paid if call['model'] == 'graph')['response_format'] == {'type': 'json_object'}
        assert before_vector == vector_hash() and before_pg == await pg_hash()
        assert (await admin.post(endpoint + '/build', json=request)).json()['id'] == job['id']
        async with container.database.sessions() as session:
            runs = list(await session.scalars(select(Run).where(Run.resource_type == 'knowledge_graph')))
            steps = list(await session.scalars(select(GraphStep)))
            assert len(runs) == len(steps) == len(provider.requests) - before
            extraction_ids = {step.run_id for step in steps if step.role == 'extract'}
            duplicate_prompts = Counter(run.fingerprint for run in runs if run.id in extraction_ids)
            assert max(duplicate_prompts.values()) >= 2
            assert all(
                run.status == 'completed'
                and run.attempt_no == 1
                and run.usage_status == 'known'
                and run.input_tokens is not None
                and run.output_tokens is not None
                for run in runs
            )
            config = await container.graph.config(
                session,
                await container.identity.principal_for_user(session, UUID(base['owner_id'])),
                UUID(base['id']),
                available=True,
            )
            assert config.active_revision == UUID(job['graph_revision']) and not config.requires_rebuild

        runner = worker.ctx['graph_runner']
        runner.limit = 0
        forced = await admin.post(
            endpoint + '/build', json={'expected_version': 1, 'client_request_id': str(uuid4()), 'rebuild': True}
        )
        assert forced.status_code == 202 and forced.json()['graph_revision'] != job['graph_revision'], forced.text
        force_cancel = await admin.post(endpoint + '/jobs/' + forced.json()['id'] + '/cancel', json={})
        assert force_cancel.status_code == 200 and force_cancel.json()['status'] == 'cancelled'

        queued = await admin.post(
            endpoint + '/preview', json={'expected_version': 1, 'client_request_id': str(uuid4()), 'document_id': None}
        )
        assert queued.status_code == 202, queued.text
        cancelled_queued = await admin.post(endpoint + '/jobs/' + queued.json()['id'] + '/cancel', json={})
        assert cancelled_queued.status_code == 200 and cancelled_queued.json()['status'] == 'cancelled', (
            cancelled_queued.text
        )
        runner.limit = 1
        provider.graph_block = True
        provider.release.clear()
        before_cancel_http = len(provider.requests)
        cancelling = await admin.post(
            endpoint + '/preview', json={'expected_version': 1, 'client_request_id': str(uuid4()), 'document_id': None}
        )
        assert cancelling.status_code == 202, cancelling.text
        cancelled_job = cancelling.json()
        async with container.database.sessions() as session:
            attempt = await session.get(GraphAttempt, UUID(cancelled_job['attempt_id']))
            generation = await session.get(GraphGeneration, UUID(cancelled_job['graph_revision']))
            cancel_manifest = GraphVectorManifest(
                UUID(base['id']), generation.id, attempt.id, generation.fingerprint, 2, attempt.workspace
            )
        for vector_role in ('entities', 'relationships', 'chunks'):
            name = cancel_manifest.collection(vector_role)
            description = cancel_manifest.description(vector_role)
            assert not milvus.has_collection(name)
            collections[name] = description
            resource('intent', 'milvus_collection', name, description=description, database='default')
        for _ in range(400):
            async with container.database.sessions() as session:
                running = await session.scalar(
                    select(Run).where(
                        Run.resource_type == 'knowledge_graph',
                        Run.resource_id == UUID(cancelled_job['attempt_id']),
                        Run.provider_started.is_(True),
                    )
                )
            if running is not None:
                break
            await asyncio.sleep(0.02)
        assert running is not None
        cancellation = await admin.post(endpoint + '/jobs/' + cancelled_job['id'] + '/cancel', json={})
        assert cancellation.status_code == 200, cancellation.text
        for _ in range(400):
            value = await admin.get(endpoint + '/jobs/' + cancelled_job['id'])
            assert value.status_code == 200, value.text
            cancelled_job = value.json()
            if cancelled_job['status'] in {'cancelled', 'failed', 'interrupted'}:
                break
            await asyncio.sleep(0.02)
        provider.release.set()
        provider.graph_block = False
        assert cancelled_job['status'] == 'cancelled', cancelled_job
        assert len(provider.requests) == before_cancel_http + 1
        async with container.database.sessions.begin() as session:
            attempt = await session.get(GraphAttempt, UUID(cancelled_job['attempt_id']))
            assert attempt.writer_stopped
            run = await session.scalar(
                select(Run).where(Run.resource_type == 'knowledge_graph', Run.resource_id == attempt.id)
            )
            assert run.status == 'cancelled' and run.provider_started and run.attempt_no == 1
            assert run.input_tokens is None and run.output_tokens is None and run.usage_status == 'unknown'
            invocation = await session.scalar(select(Invocation).where(Invocation.run_id == run.id))
            assert invocation.status == 'unknown' and invocation.reserved_cost > 0
            generation = await session.get(GraphGeneration, UUID(cancelled_job['graph_revision']))
            generation.gc_after = utcnow() - timedelta(seconds=1)
            pin = GraphPin(
                generation_id=generation.id, owner_token=uuid4(), lease_until=utcnow() + timedelta(seconds=60)
            )
            session.add(pin)
            await session.flush()
            pin_id = pin.id
        await container.graph_jobs.gc(UUID(cancelled_job['graph_revision']))
        assert all(
            milvus.has_collection(cancel_manifest.collection(role)) for role in ('entities', 'relationships', 'chunks')
        )
        from tests.core.graph_transport import delayed_commit_transport
        from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC
        from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl
        from pymilvus import CollectionSchema, FieldSchema
        from zhiheng.shared.errors import AppError

        description = milvus.describe_collection(cancel_manifest.collection('entities'))
        schema = CollectionSchema(
            fields=[
                FieldSchema(
                    name=field['name'],
                    dtype=field['type'],
                    is_primary=field.get('is_primary', False),
                    nullable=field.get('nullable', False),
                    **field.get('params', {}),
                )
                for field in description['fields']
            ],
            description=cancel_manifest.description('entities'),
            enable_dynamic_field=True,
        )
        with delayed_commit_transport() as (proxy, entered, release, committed):
            rpc = GraphVectorRPC(uri=proxy, token='', database='default', control=VectorIOControl(timeout_seconds=0.25))
            try:
                with pytest.raises(AppError):
                    await rpc.acall(
                        'create_collection', cancel_manifest.collection('entities'), schema=schema, timeout=0.25
                    )
                assert entered.is_set() and not committed.is_set()
            finally:
                await rpc.aclose()
            assert rpc.stopped
            late = await asyncpg.connect(url.render_as_string(hide_password=False))
            transaction = late.transaction()
            await transaction.start()
            try:
                await late.execute(
                    "INSERT INTO knowledge_graph_sdk.lightrag_graph_nodes(workspace,id,properties) VALUES ($1,'late-accepted','{}')",
                    cancel_manifest.workspace,
                )
                async with container.database.sessions.begin() as session:
                    await container.graph_store.release_pin(session, pin_id)
                await container.graph_jobs.gc(UUID(cancelled_job['graph_revision']))
                assert all(
                    not milvus.has_collection(cancel_manifest.collection(role))
                    for role in ('entities', 'relationships', 'chunks')
                )
                await transaction.commit()
                release.set()
                assert await asyncio.to_thread(committed.wait, 5)
            finally:
                await late.close()
        assert milvus.has_collection(cancel_manifest.collection('entities'))
        assert (
            await pg.fetchval(
                'SELECT count(*) FROM knowledge_graph_sdk.lightrag_graph_nodes WHERE workspace=$1',
                cancel_manifest.workspace,
            )
            == 1
        )
        async with container.database.sessions.begin() as session:
            generation = await session.get(GraphGeneration, UUID(cancelled_job['graph_revision']))
            assert generation.status == 'collected' and generation.gc_after > utcnow()
            generation.gc_after = utcnow() - timedelta(seconds=1)
            pin = GraphPin(
                generation_id=generation.id, owner_token=uuid4(), lease_until=utcnow() + timedelta(seconds=60)
            )
            session.add(pin)
            await session.flush()
            late_pin = pin.id
        await container.graph_jobs.gc(UUID(cancelled_job['graph_revision']))
        assert milvus.has_collection(cancel_manifest.collection('entities'))
        async with container.database.sessions.begin() as session:
            await container.graph_store.release_pin(session, late_pin)
        await container.graph_jobs.gc(UUID(cancelled_job['graph_revision']))
        assert all(
            not milvus.has_collection(cancel_manifest.collection(role))
            for role in ('entities', 'relationships', 'chunks')
        )
        assert (
            await pg.fetchval(
                'SELECT count(*) FROM knowledge_graph_sdk.lightrag_graph_nodes WHERE workspace=$1',
                cancel_manifest.workspace,
            )
            == 0
        )
        assert before_vector == vector_hash() and before_pg == await pg_hash()
        original_invoke = container.graph_runs.invoke
        epoch_lost = asyncio.Event()
        epoch_injection_started = False
        lost_pg_digest = None

        async def lose_epoch(claim, call):
            nonlocal lost_pg_digest, epoch_injection_started
            value = await original_invoke(claim, call)
            if call.role == 'extract' and not epoch_injection_started:
                epoch_injection_started = True
                async with container.database.sessions.begin() as session:
                    # Hold the original canonical owner locks while measuring
                    # the last valid namespace. No callback may observe loss
                    # and cancel this instrumentation before its baseline exists.
                    parent, attempt = await container.graph_store._owned(session, claim)
                    lost_pg_digest = await pg_hash(claim.workspace)
                    attempt.epoch += 1
                    attempt.status = 'interrupted'
                    parent.status = 'interrupted'
                    parent.error = {'code': 'test_lease_lost', 'message': '测试执行权失效'}
                    parent.finished_at = utcnow()
                    # The synchronous hook runs only after the actual commit,
                    # even if the SDK cancels this coroutine as soon as loss is visible.
                    event.listen(session.sync_session, 'after_commit', lambda _session: epoch_lost.set(), once=True)
            return value

        monkeypatch.setattr(container.graph_runs, 'invoke', lose_epoch)
        fenced = await admin.post(
            endpoint + '/preview', json={'expected_version': 1, 'client_request_id': str(uuid4()), 'document_id': None}
        )
        assert fenced.status_code == 202, fenced.text
        fenced_job = fenced.json()
        async with container.database.sessions() as session:
            attempt = await session.get(GraphAttempt, UUID(fenced_job['attempt_id']))
            generation = await session.get(GraphGeneration, UUID(fenced_job['graph_revision']))
            fenced_manifest = GraphVectorManifest(
                UUID(base['id']), generation.id, attempt.id, generation.fingerprint, 2, attempt.workspace
            )
        for vector_role in ('entities', 'relationships', 'chunks'):
            name = fenced_manifest.collection(vector_role)
            description = fenced_manifest.description(vector_role)
            collections[name] = description
            resource('intent', 'milvus_collection', name, description=description, database='default')
        await asyncio.wait_for(epoch_lost.wait(), 20)
        for _ in range(400):
            async with container.database.sessions() as session:
                stopped_attempt = await session.get(GraphAttempt, UUID(fenced_job['attempt_id']))
                if stopped_attempt.writer_stopped:
                    break
            await asyncio.sleep(0.02)
        assert stopped_attempt.writer_stopped and stopped_attempt.status == 'interrupted'
        assert lost_pg_digest == await pg_hash(fenced_manifest.workspace)
        assert (
            await pg.fetchval(
                'SELECT count(*) FROM knowledge_graph_sdk.lightrag_graph_nodes WHERE workspace=$1',
                fenced_manifest.workspace,
            )
            == 0
        )
        assert (
            await pg.fetchval(
                'SELECT count(*) FROM knowledge_graph_sdk.lightrag_graph_edges WHERE workspace=$1',
                fenced_manifest.workspace,
            )
            == 0
        )
        for vector_role in ('entities', 'relationships', 'chunks'):
            assert milvus.query(
                fenced_manifest.collection(vector_role),
                filter='',
                output_fields=['count(*)'],
                consistency_level='Strong',
            ) == [{'count(*)': 0}]
        assert before_vector == vector_hash() and before_pg == await pg_hash()
        monkeypatch.setattr(container.graph_runs, 'invoke', original_invoke)
        async with member_client(app) as member:
            assert (await member.get(endpoint + '/labels')).status_code == 200
            reader = (await member.get('/api/auth/me')).json()
            async with container.database.sessions() as session:
                original_owner_count = await session.scalar(
                    select(func.count()).select_from(Invocation).where(Invocation.user_id == UUID(base['owner_id']))
                )
            reader_query = await member.post(
                endpoint + '/query',
                json={'client_request_id': str(uuid4()), 'query': '合作', 'mode': 'mix', 'top_k': 2},
            )
            assert reader_query.status_code == 202, reader_query.text
            reader_job = reader_query.json()
            for _ in range(600):
                status = await member.get(endpoint + '/jobs/' + reader_job['id'])
                assert status.status_code == 200, status.text
                reader_job = status.json()
                if reader_job['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
                    break
                await asyncio.sleep(0.02)
            assert reader_job['status'] == 'completed', reader_job
            assert (await admin.get(endpoint + '/jobs/' + reader_job['id'])).status_code == 404
            async with container.database.sessions() as session:
                reader_runs = list(
                    await session.scalars(
                        select(Run).where(
                            Run.resource_type == 'knowledge_graph', Run.resource_id == UUID(reader_job['attempt_id'])
                        )
                    )
                )
                assert len(reader_runs) == 2 and all(
                    row.owner_id == UUID(reader['id']) and row.billing_owner_id == UUID(reader['id'])
                    for row in reader_runs
                )
                assert (
                    await session.scalar(
                        select(func.count()).select_from(Invocation).where(Invocation.user_id == UUID(base['owner_id']))
                    )
                    == original_owner_count
                )
                for row in reader_runs:
                    invocation = await session.scalar(select(Invocation).where(Invocation.run_id == row.id))
                    assert invocation.user_id == UUID(reader['id']) and invocation.status == 'known'
            info = (await admin.get('/api/files/' + document['id'])).json()
            revoke = await admin.patch(
                '/api/files/' + document['id'] + '/acl',
                json={'expected_version': info['acl_version'], 'access': 'private', 'entries': []},
            )
            assert revoke.status_code == 200, revoke.text
            http_before = len(provider.requests)
            for suffix in ('/labels', '/subgraph', '/jobs/' + job['id']):
                denied = await member.get(endpoint + suffix)
                assert denied.status_code in {403, 404, 409}, denied.text
            assert (await member.post(endpoint + '/jobs/' + job['id'] + '/cancel', json={})).status_code in {
                403,
                404,
                409,
            }
            denied = await member.post(
                endpoint + '/query',
                json={'client_request_id': str(uuid4()), 'query': '合作', 'mode': 'mix', 'top_k': 2},
            )
            assert denied.status_code in {403, 404, 409}, denied.text
            assert len(provider.requests) == http_before
            for child in reader_runs:
                assert (await member.get('/api/runs/' + str(child.id))).status_code in {403, 404, 409}
                assert (await member.post('/api/runs/' + str(child.id) + '/cancel', json={})).status_code in {
                    403,
                    404,
                    409,
                }
                assert (await member.get('/api/runs/' + str(child.id) + '/events')).status_code in {403, 404, 409}
            info = (await admin.get('/api/files/' + document['id'])).json()
            restored = await admin.patch(
                '/api/files/' + document['id'] + '/acl',
                json={'expected_version': info['acl_version'], 'access': 'public', 'entries': []},
            )
            assert restored.status_code == 200, restored.text
            assert (await member.get(endpoint + '/labels')).status_code == 200
            assert (await member.get(endpoint + '/jobs/' + reader_job['id'])).status_code == 200
            assert (await admin.get(endpoint + '/config')).json()['requires_rebuild'] is False
            for child in reader_runs:
                assert (await member.get('/api/runs/' + str(child.id))).status_code == 200
            assert len(provider.requests) == http_before
        runner.limit = 0
        changed = await admin.patch(
            endpoint + '/config', json={'expected_version': 1, 'llm_model_id': model['id'], 'max_output_tokens': 4096}
        )
        assert changed.status_code == 200, changed.text
        next_job = await admin.post(
            endpoint + '/build', json={'expected_version': 2, 'client_request_id': str(uuid4()), 'rebuild': True}
        )
        assert next_job.status_code == 202, next_job.text
        history = await admin.get(endpoint + '/jobs')
        assert history.status_code == 200, history.text
        assert {job['id'], next_job.json()['id']} <= {row['id'] for row in history.json()}
        past = await admin.get(endpoint + '/jobs/' + job['id'])
        assert past.status_code == 200 and past.json()['status'] == 'completed' and past.json()['result'], past.text
        assert (await admin.post(endpoint + '/jobs/' + next_job.json()['id'] + '/cancel', json={})).status_code == 200
        original = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
        removal = await admin.request(
            'DELETE',
            f'/api/knowledge/bases/{base["id"]}',
            json={'expected_version': original['version'], 'client_request_id': str(uuid4())},
        )
        assert removal.status_code == 202, removal.text
        async with container.database.sessions.begin() as session:
            from zhiheng.modules.knowledge.graph.orm import GraphConfiguration

            config = await session.get(GraphConfiguration, UUID(base['id']))
            assert config.active_revision is None
            versions = list(
                await session.scalars(select(GraphGeneration).where(GraphGeneration.base_id == UUID(base['id'])))
            )
            for version in versions:
                if version.status == 'retired':
                    version.gc_after = utcnow() - timedelta(seconds=1)
        for version in versions:
            await container.graph_jobs.gc(version.id)
        assert all(not milvus.has_collection(name) for name in collections)
        assert (await admin.get(endpoint + '/labels')).status_code == 404
    finally:
        provider.release.set()
        provider.graph_block = False
        if worker is not None:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await worker.close()
        await container.graph_sdk.close()
        for name, description in collections.items():
            if milvus.has_collection(name):
                assert milvus.describe_collection(name)['description'] == description
                milvus.drop_collection(name)
            assert not milvus.has_collection(name)
            resource('deleted', 'milvus_collection', name)
        milvus.close()
        keys.update({queue, queue + ':heartbeat', queue + ':arq-health'})
        for key in keys:
            await container.redis.delete(key)
            assert not await container.redis.exists(key)
            resource('deleted', 'redis_key', key)
        await pg.execute(f'DROP OWNED BY "{role}"')
        await pg.execute(f'DROP ROLE "{role}"')
        resource('deleted', 'postgres_role', role, database=url.database)
        await pg.close()
