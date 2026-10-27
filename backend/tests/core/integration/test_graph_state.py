from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.provider import ProviderServer
from zhiheng.modules.knowledge.graph.contracts import GraphBuildRequest, GraphConfigPatch
from zhiheng.modules.knowledge.graph.orm import GraphAttempt, GraphConfiguration, GraphGeneration
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


async def test_durable_epoch_queued_cancel_expiry_publish_cas_and_gc_fence(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str
) -> None:
    container = app.state.container
    base = await ready_base(admin, app, provider)
    base_id = UUID(base['id'])
    async with container.database.sessions.begin() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
        await container.graph.patch(
            session, owner, base_id, GraphConfigPatch(expected_version=0, llm_model_id=UUID(model_id))
        )
    before = list(provider.requests)
    async with container.database.sessions.begin() as session:
        frozen = await container.graph.freeze(session, owner, base_id)
        changed = frozen.model_copy(
            update={'chunks': [frozen.chunks[0].model_copy(update={'content': 'changed after freeze'})]}
        )
        with pytest.raises(AppError, match='指纹'):
            await container.graph.authorize(session, owner, base_id, changed)

    async with container.database.sessions.begin() as session:
        job = await container.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )
    async with container.database.sessions.begin() as session:
        cancelled = await container.graph.cancel(session, owner, base_id, job.id)
        assert cancelled.status == 'cancelled'
        attempt = await session.get(GraphAttempt, job.attempt_id)
        assert attempt.writer_stopped and attempt.owner_token is None
        assert await container.graph_store.claim_gc(session, job.graph_revision) == {}
    async with container.database.sessions.begin() as session:
        second = await container.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )
        claim = await container.graph_store.claim(session, second.id)
        assert claim is not None
    async with container.database.sessions.begin() as session:
        attempt = await session.get(GraphAttempt, claim.attempt_id)
        attempt.lease_until = utcnow() - timedelta(seconds=1)
    async with container.database.sessions.begin() as session:
        await container.graph_store.recover(session)
    async with container.database.sessions.begin() as session:
        attempt = await session.get(GraphAttempt, claim.attempt_id)
        assert attempt.status == 'interrupted' and attempt.epoch == claim.epoch + 1 and not attempt.writer_stopped
        generation = await session.get(GraphGeneration, claim.revision)
        generation.status = 'retired'
        generation.gc_after = utcnow() - timedelta(seconds=1)
        assert await container.graph_store.claim_gc(session, claim.revision) is None
    with pytest.raises(AppError, match='执行权'):
        async with container.database.sessions.begin() as session:
            await container.graph_store.require_owned(session, claim)
    async with container.database.sessions.begin() as session:
        retry = await container.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )
        current = await container.graph_store.claim(session, retry.id)
        assert current is not None and current.workspace != claim.workspace
    with pytest.raises(AppError, match='尚未停止'):
        async with container.database.sessions.begin() as session:
            await container.graph_store.finish(
                session,
                current,
                status='completed',
                result={'chunks': 1, 'entities': 1, 'relations': 0, 'seconds': 0.0},
                error=None,
                stopped=False,
            )
    async with container.database.sessions.begin() as session:
        await container.graph.patch(
            session, owner, base_id, GraphConfigPatch(expected_version=1, llm_model_id=UUID(model_id))
        )
    with pytest.raises(AppError, match='已变化'):
        async with container.database.sessions.begin() as session:
            await container.graph_store.finish(
                session,
                current,
                status='completed',
                result={'chunks': 1, 'entities': 1, 'relations': 0, 'seconds': 0.0},
                error=None,
                stopped=True,
            )
    async with container.database.sessions() as session:
        config = await session.get(GraphConfiguration, base_id)
        assert config.active_revision is None
    async with container.database.sessions.begin() as session:
        await container.graph_store.finish(
            session,
            current,
            status='interrupted',
            result=None,
            error={'code': 'test_stop', 'message': '测试已停止'},
            stopped=True,
        )
        latest = await container.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=2, client_request_id=uuid4()), 'build'
        )
        history = await container.graph.jobs(session, owner, base_id)
        assert {job.id, second.id, retry.id, latest.id} <= {row.id for row in history}
        assert (await container.graph.job(session, owner, base_id, job.id)).status == 'cancelled'
    assert provider.requests == before
