"""The real pinned LightRAG SDK delegates reranking to governed, metered Runs."""

import asyncio
import os
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.graph_fixtures import graph_role
from tests.core.integration.test_knowledge_retrieval_v2 import ready
from tests.core.provider import ProviderServer
from tests.core.resources import resource
from zhiheng.modules.knowledge.graph.contracts import GraphBuildRequest, GraphConfigPatch, GraphQueryRequest
from zhiheng.modules.knowledge.graph.orm import GraphGeneration, GraphStep
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


async def pump(container: Any, job_id: UUID) -> None:
    task = asyncio.create_task(container.graph_jobs.execute(job_id))
    try:
        async with asyncio.timeout(50):
            while not task.done():
                async with container.database.sessions() as session:
                    pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
                for run_id in pending:
                    await execute_run(container, run_id)
                await asyncio.sleep(0.03)
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_real_lightrag_query_calls_governed_ranker_and_keeps_sources(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus required')
    base, _, rerank, _ = await ready(admin, app, provider)
    container = app.state.container
    connection = (
        await admin.post(
            '/api/models/connections',
            json={'name': 'Graph mock', 'base_url': f'http://127.0.0.1:{provider.server_port}/v1', 'api_key': 'mock'},
        )
    ).json()
    model = (
        await admin.post(
            '/api/models',
            json={
                'name': 'Graph mock',
                'connection_id': connection['id'],
                'upstream_model': 'graph',
                'max_output_tokens': 2048,
            },
        )
    ).json()
    async with graph_role(container.settings) as (_, runtime, _):
        sdk = container.graph_sdk
        sdk.database_url, sdk.uri = runtime, uri
        owner_id, base_id = UUID(base['owner_id']), UUID(base['id'])
        async with container.database.sessions.begin() as session:
            owner = await container.identity.principal_for_user(session, owner_id)
            await container.graph.patch(
                session,
                owner,
                base_id,
                GraphConfigPatch(
                    expected_version=0, llm_model_id=UUID(model['id']), max_output_tokens=2048, concurrency=1
                ),
            )
        manifests = []
        try:
            build = await container.graph_jobs.submit(
                owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
            )
            await pump(container, build.id)
            async with container.database.sessions() as session:
                result = await container.graph.job(session, owner, base_id, build.id)
                assert result.status == 'completed', result
                generations = list(await session.scalars(select(GraphGeneration)))
                manifests = [generation.manifest for generation in generations]
            query = await container.graph_jobs.submit(
                owner, base_id, GraphQueryRequest(query='甲机构参与什么合作', client_request_id=uuid4()), 'query'
            )
            await pump(container, query.id)
            async with container.database.sessions() as session:
                result = await container.graph.job(session, owner, base_id, query.id)
                assert result.status == 'completed', result
                assert result.result.diagnostics['rerank_status'] == 'completed'
                assert 0 < len(result.result.sources) <= 8
                steps = list(
                    await session.scalars(
                        select(GraphStep).where(GraphStep.attempt_id == query.attempt_id, GraphStep.role == 'rerank')
                    )
                )
                assert len(steps) == 1
                invocation = await session.get(Run, steps[0].run_id)
                assert invocation.payload['binding']['model_id'] == rerank
            assert len([body for body in provider.requests if body['model'] == 'ranker']) == 1
            # Viewing a completed graph query reuses its persisted result and usage.
            async with container.database.sessions() as session:
                await container.graph.job(session, owner, base_id, query.id)
            assert len([body for body in provider.requests if body['model'] == 'ranker']) == 1
        finally:
            from zhiheng.modules.knowledge.graph.contracts import GraphVectorManifest

            for manifest in manifests:
                value = GraphVectorManifest.load(manifest)
                await sdk.gc(value)
                for role in ('entities', 'relationships', 'chunks'):
                    resource('deleted', 'milvus_collection', value.collection(role))
