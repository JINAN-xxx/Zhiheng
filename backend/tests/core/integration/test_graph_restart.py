import asyncio
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.engine import make_url

from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.provider import ProviderServer
from tests.core.resources import resource
from zhiheng.modules.knowledge.adapters.lightrag.vector import GraphVectorManifest
from zhiheng.modules.knowledge.graph.orm import GraphAttempt, GraphGeneration
from zhiheng.runtime.orm import Run
from zhiheng.worker.main import build_worker


async def test_actual_worker_kill_interruption_and_explicit_retry_use_new_namespace(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    tmp_path: Path,
) -> None:
    from pymilvus import MilvusClient

    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus required')
    container = app.state.container
    base = await ready_base(admin, app, provider)
    connection = (
        await admin.post(
            '/api/models/connections',
            json={
                'name': 'Restart graph',
                'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
                'api_key': 'local',
            },
        )
    ).json()
    model = (
        await admin.post(
            '/api/models',
            json={
                'name': 'Restart graph',
                'connection_id': connection['id'],
                'upstream_model': 'graph',
                'max_output_tokens': 4096,
            },
        )
    ).json()
    url = make_url(container.settings.database_url.get_secret_value().replace('postgresql+asyncpg:', 'postgresql:'))
    pg = await asyncpg.connect(url.render_as_string(hide_password=False))
    role = 'graph_run_' + uuid4().hex
    password = secrets.token_urlsafe(24)
    resource('intent', 'postgres_role', role, database=url.database)
    collections = {}
    process = None
    worker = None
    worker_task = None
    config_path = tmp_path / 'worker.json'
    stream = None
    client = MilvusClient(uri=uri, timeout=5)

    async def manifest_for(job):
        async with container.database.sessions() as session:
            attempt = await session.get(GraphAttempt, UUID(job['attempt_id']))
            generation = await session.get(GraphGeneration, UUID(job['graph_revision']))
            manifest = GraphVectorManifest(
                UUID(base['id']), generation.id, attempt.id, generation.fingerprint, 2, attempt.workspace
            )
        for vector_role in ('entities', 'relationships', 'chunks'):
            name = manifest.collection(vector_role)
            description = manifest.description(vector_role)
            assert not client.has_collection(name)
            collections[name] = description
            resource('intent', 'milvus_collection', name, description=description, database='default')
        return manifest

    try:
        await pg.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\'')
        resource('created', 'postgres_role', role, database=url.database)
        await pg.execute(f'REVOKE CREATE,TEMPORARY ON DATABASE "{url.database}" FROM PUBLIC')
        await pg.execute(f'GRANT USAGE ON SCHEMA knowledge_graph_sdk TO "{role}"')
        await pg.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA knowledge_graph_sdk TO "{role}"')
        runtime = url.set(username=role, password=password).render_as_string(hide_password=False)
        container.graph_sdk.database_url = runtime
        container.graph_sdk.uri = uri
        endpoint = f'/api/knowledge/bases/{base["id"]}/graph'
        configured = await admin.patch(
            endpoint + '/config', json={'expected_version': 0, 'llm_model_id': model['id'], 'max_output_tokens': 4096}
        )
        assert configured.status_code == 200, configured.text
        body = {'expected_version': 1, 'client_request_id': str(uuid4()), 'rebuild': True}
        first = await admin.post(endpoint + '/build', json=body)
        assert first.status_code == 202, first.text
        job = first.json()
        old_manifest = await manifest_for(job)
        configuration = container.settings.model_dump(mode='json')
        for name in ('database_url', 'redis_url', 'credential_key', 'milvus_token'):
            configuration[name] = getattr(container.settings, name).get_secret_value()
        configuration.update(graph_database_url=runtime, milvus_uri=uri)
        descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(configuration, output)
        resource('created', 'private_config', str(config_path))
        log_path = (
            Path(os.environ['ZHIHENG_TEST_RESOURCE_LOG']).parent / 'evidence' / ('worker-kill-' + uuid4().hex + '.log')
        )
        stream = os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb')
        provider.graph_block = True
        provider.release.clear()
        before = len(provider.requests)
        resource('intent', 'worker_process', container.settings.worker_queue)
        process = subprocess.Popen(
            [sys.executable, '-B', '-m', 'tests.core.graph_worker_process', str(config_path)],
            cwd=Path(__file__).resolve().parents[3],
            env=os.environ.copy(),
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        resource('created', 'worker_process', str(process.pid), queue=container.settings.worker_queue)
        for _ in range(3000):
            if await container.redis.exists(container.settings.worker_queue + ':arq-health'):
                break
            if process.poll() is not None:
                raise AssertionError('Worker exited during startup')
            await asyncio.sleep(0.02)
        started = None
        for _ in range(1000):
            async with container.database.sessions() as session:
                started = await session.scalar(
                    select(Run).where(
                        Run.resource_type == 'knowledge_graph',
                        Run.resource_id == UUID(job['attempt_id']),
                        Run.provider_started.is_(True),
                    )
                )
            if started is not None and len(provider.requests) > before:
                break
            if process.poll() is not None:
                raise AssertionError('Worker exited before provider start')
            await asyncio.sleep(0.02)
        assert started is not None and len(provider.requests) == before + 1, (
            await admin.get(endpoint + '/jobs/' + job['id'])
        ).json()
        process.kill()
        assert await asyncio.to_thread(process.wait, timeout=5) == -9
        resource('deleted', 'worker_process', str(process.pid))
        process = None
        provider.release.set()
        provider.graph_block = False
        assert len(provider.requests) == before + 1
        worker = build_worker(container, max_jobs=1)
        worker_task = asyncio.create_task(worker.async_run())
        for _ in range(800):
            polled = await admin.get(endpoint + '/jobs/' + job['id'])
            assert polled.status_code == 200, polled.text
            if polled.json()['status'] == 'interrupted':
                break
            await asyncio.sleep(0.02)
        assert polled.json()['status'] == 'interrupted', polled.text
        assert len(provider.requests) == before + 1
        async with container.database.sessions() as session:
            old_attempt = await session.get(GraphAttempt, UUID(job['attempt_id']))
            assert old_attempt.epoch > 1 and not old_attempt.writer_stopped
            old_run = await session.get(Run, started.id)
            assert old_run.status == 'interrupted' and old_run.usage_status == 'unknown' and old_run.attempt_no == 1
        worker.ctx['graph_runner'].limit = 0
        retry = await admin.post(endpoint + '/build', json={**body, 'client_request_id': str(uuid4())})
        assert retry.status_code == 202, retry.text
        current = retry.json()
        new_manifest = await manifest_for(current)
        assert new_manifest.workspace != old_manifest.workspace and current['attempt_id'] != job['attempt_id']
        worker.ctx['graph_runner'].limit = 1
        for _ in range(1000):
            polled = await admin.get(endpoint + '/jobs/' + current['id'])
            assert polled.status_code == 200, polled.text
            current = polled.json()
            if current['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
                break
            await asyncio.sleep(0.02)
        assert current['status'] == 'completed', current
        assert (await admin.get(endpoint + '/config')).json()['active_revision'] == current['graph_revision']
        assert (await admin.get(endpoint + '/jobs/' + job['id'])).json()['status'] == 'interrupted'
    finally:
        provider.release.set()
        provider.graph_block = False
        if process is not None:
            process.kill()
            await asyncio.to_thread(process.wait, timeout=5)
            resource('deleted', 'worker_process', str(process.pid))
        if worker_task is not None:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        if worker is not None:
            await worker.close()
        await container.graph_sdk.close()
        if stream is not None:
            stream.close()
        config_path.unlink(missing_ok=True)
        resource('deleted', 'private_config', str(config_path))
        for name, description in collections.items():
            if client.has_collection(name):
                assert client.describe_collection(name)['description'] == description
                client.drop_collection(name)
            assert not client.has_collection(name)
            resource('deleted', 'milvus_collection', name)
        client.close()
        await pg.execute(f'DROP OWNED BY "{role}"')
        await pg.execute(f'DROP ROLE "{role}"')
        resource('deleted', 'postgres_role', role, database=url.database)
        await pg.close()
