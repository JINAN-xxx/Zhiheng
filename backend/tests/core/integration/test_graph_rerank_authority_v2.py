"""Model revocation after provider completion cannot authorize graph fallback or delivery."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_knowledge_retrieval_v2 import ready
from tests.core.provider import ProviderHandler, ProviderServer
from zhiheng.application.knowledge.graph.runs import GraphModelCall
from zhiheng.modules.knowledge.graph.contracts import (
    GraphBuildRequest,
    GraphConfigPatch,
    GraphQueryRequest,
    GraphVectorManifest,
)
from zhiheng.modules.knowledge.graph.orm import GraphStep
from zhiheng.modules.models.orm import ModelConnection, ModelDefinition
from zhiheng.modules.models.public import ModelMessage
from zhiheng.shared.errors import AppError
from zhiheng.worker.execution import execute_run


async def query_claim(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str
) -> tuple[Any, str]:
    base, _, rerank, _ = await ready(admin, app, provider)
    c = app.state.container
    base_id = UUID(base['id'])
    async with c.database.sessions.begin() as session:
        owner = await c.identity.principal_for_user(session, UUID(base['owner_id']))
        await c.graph.patch(session, owner, base_id, GraphConfigPatch(expected_version=0, llm_model_id=UUID(model_id)))
        build = await c.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )
    async with c.database.sessions.begin() as session:
        build_claim = await c.graph_store.claim(session, build.id)
        manifest = GraphVectorManifest(
            base_id,
            build_claim.revision,
            build_claim.attempt_id,
            build_claim.frozen.fingerprint,
            build_claim.frozen.dimension,
            build_claim.workspace,
        )
        await c.graph_store.save_manifest(session, build_claim, manifest.dump())
        # The build port's success is synthetic; this regression exercises only
        # graph authority and real governed model Runs, without any SDK storage.
        await c.graph_store.finish(
            session,
            build_claim,
            status='completed',
            result={'entities': 0, 'relations': 0, 'chunks': 2, 'seconds': 0},
            error=None,
            stopped=True,
        )
    async with c.database.sessions.begin() as session:
        query = await c.graph.admit(
            session, owner, base_id, GraphQueryRequest(query='项目编号', client_request_id=uuid4()), 'query'
        )
        claim = await c.graph_store.claim(session, query.id)
    return claim, rerank


async def revoke(container: Any, model_id: str, mutation: str) -> None:
    async with container.database.sessions.begin() as session:
        model = await session.get(ModelDefinition, UUID(model_id))
        if mutation == 'disable':
            model.enabled = False
        elif mutation == 'connection':
            connection = await session.get(ModelConnection, model.connection_id)
            connection.enabled = False
        else:
            model.upstream_model = 'changed-after-provider'


@pytest.mark.parametrize(
    'result,mutation', [('completed', 'disable'), ('transient', 'connection'), ('completed', 'binding')]
)
async def test_graph_terminal_ranker_consumption_rechecks_current_binding(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
    result: str,
    mutation: str,
) -> None:
    claim, rerank = await query_claim(admin, app, provider, model_id)
    c = app.state.container
    entered, release = asyncio.Event(), asyncio.Event()
    original_owned, original_provider = c.graph_store.require_owned, ProviderHandler.structured

    async def gated_owned(session: Any, current: Any) -> None:
        entered.set()
        await release.wait()
        await original_owned(session, current)

    def structured(handler: ProviderHandler, body: dict[str, Any]) -> None:
        if result == 'transient' and handler.path.endswith('/rerank'):
            handler.send_response(429)
            handler.send_header('Content-Type', 'application/json')
            handler.end_headers()
            handler.wfile.write(b'{"error":{"message":"temporary mock failure"}}')
        else:
            original_provider(handler, body)

    monkeypatch.setattr(c.graph_store, 'require_owned', gated_owned)
    monkeypatch.setattr(ProviderHandler, 'structured', structured)
    c.graph_runs.start_attempt(claim)
    call = GraphModelCall(
        'rerank',
        'rerank',
        (
            ModelMessage('user', str(claim.request['query'])),
            *(ModelMessage('user', chunk.content) for chunk in claim.frozen.chunks),
        ),
    )
    task = asyncio.create_task(c.graph_runs.invoke(claim, call))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        async with c.database.sessions() as session:
            step = await session.scalar(
                select(GraphStep).where(GraphStep.attempt_id == claim.attempt_id, GraphStep.role == 'rerank')
            )
        await execute_run(c, step.run_id)
        await revoke(c, rerank, mutation)
        release.set()
        with pytest.raises(AppError):
            await asyncio.wait_for(task, 3)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        c.graph_runs.release(claim.attempt_id)


async def test_graph_query_finish_rechecks_ranker_after_callback_return(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
) -> None:
    claim, rerank = await query_claim(admin, app, provider, model_id)
    c = app.state.container
    await revoke(c, rerank, 'disable')
    with pytest.raises(AppError):
        async with c.database.sessions.begin() as session:
            await c.graph_store.finish(
                session,
                claim,
                status='completed',
                result={'context': 'would leak', 'entities': [], 'relationships': [], 'sources': []},
                error=None,
                stopped=True,
            )
