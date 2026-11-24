from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI

from tests.core.integration.test_knowledge_failures import ready_base
from tests.core.provider import ProviderServer
from zhiheng.application.knowledge.graph.runs import GraphModelCall
from zhiheng.modules.knowledge.graph.contracts import (
    GraphBuildRequest,
    GraphConfig,
    GraphConfigPatch,
    GraphPreviewRequest,
)
from zhiheng.modules.knowledge.graph.orm import GraphAttempt, GraphConfiguration, GraphGeneration, GraphJob
from zhiheng.modules.knowledge.graph.service import GraphService
from zhiheng.modules.models.orm import ConnectionRevision
from zhiheng.modules.models.public import ModelMessage
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


def test_graph_job_budget_scales_by_batch_and_keeps_queries_bounded() -> None:
    config = GraphConfig(version=1, llm_model_id=uuid4(), timeout_seconds=240, concurrency=2)
    assert GraphService._job_timeout_seconds(config, None) == 240
    assert GraphService._job_timeout_seconds(config, 3) == 1260
    assert GraphService._job_timeout_seconds(config, 30) == 7200
    assert GraphService._job_timeout_seconds(config, 1000) == 7200


async def test_graph_low_reasoning_uses_the_verified_daip_connection(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str
) -> None:
    container = app.state.container
    base = await ready_base(admin, app, provider)
    base_id = UUID(base['id'])
    models = (await admin.get('/api/models')).json()
    connection_id = next(item['connection_id'] for item in models if item['id'] == model_id)
    created = await admin.post(
        '/api/models',
        json={
            'name': 'Extraction model',
            'upstream_model': 'GLM-5.3',
            'connection_id': connection_id,
            'max_output_tokens': 16384,
        },
    )
    assert created.status_code == 201, created.text
    extraction_id = UUID(created.json()['id'])
    low = GraphConfigPatch(expected_version=0, llm_model_id=extraction_id, reasoning_strategy='low')
    async with container.database.sessions.begin() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
        with pytest.raises(AppError, match='不支持'):
            await container.graph.patch(session, owner, base_id, low)
        binding = await container.models.bindings.resolve(session, extraction_id)
        revision = await session.get(ConnectionRevision, binding.connection_revision_id)
        assert revision is not None
        revision.base_url = 'https://zhenze-huhehaote.cmecloud.cn/tokenplan/v1'
        await session.flush()
        with pytest.raises(AppError, match='不支持'):
            await container.graph.patch(
                session, owner, base_id, low.model_copy(update={'reasoning_strategy': 'disabled'})
            )
        await container.graph.patch(session, owner, base_id, low)
        await session.flush()
        info = await container.graph.config(session, owner, base_id, available=True)
        assert 'low' in info.supported_reasoning_strategies
        assert 'disabled' not in info.supported_reasoning_strategies
        queued = await container.graph.admit(
            session, owner, base_id, GraphPreviewRequest(expected_version=1, client_request_id=uuid4()), 'preview'
        )
        claim = await container.graph_store.claim(session, queued.id)
        assert claim is not None
        call = GraphModelCall('extract', 'generation', (ModelMessage('user', 'Extract a relation.'),))
        assert container.graph_jobs.runs._binding(claim, call).options.reasoning_effort == 'low'


async def test_graph_keeps_known_failure_when_cleanup_outlives_the_lease(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    base = await ready_base(admin, app, provider)
    base_id = UUID(base['id'])
    async with container.database.sessions.begin() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
        await container.graph.patch(
            session, owner, base_id, GraphConfigPatch(expected_version=0, llm_model_id=UUID(model_id))
        )
        queued = await container.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )

    async def fail(*args: object) -> None:
        raise AppError('graph_model_truncated', '图谱抽取或摘要被输出上限截断', 422)

    async def drain(claim: object) -> None:
        async with container.database.sessions.begin() as session:
            attempt = await session.get(GraphAttempt, queued.attempt_id)
            assert attempt is not None
            attempt.lease_until = utcnow() - timedelta(seconds=1)
            await session.flush()
            await container.graph_store.recover(session)

    monkeypatch.setattr(container.graph_jobs, '_execute', fail)
    monkeypatch.setattr(container.graph_jobs.runs, 'drain', drain)
    await container.graph_jobs.execute(queued.id)

    async with container.database.sessions() as session:
        job = await session.get(GraphJob, queued.id)
        attempt = await session.get(GraphAttempt, queued.attempt_id)
        config = await session.get(GraphConfiguration, base_id)
        assert job is not None and job.status == 'failed'
        assert job.error == {'code': 'graph_model_truncated', 'message': '图谱抽取或摘要被输出上限截断'}
        assert attempt is not None and attempt.status == 'interrupted'
        assert config is not None and config.active_revision is None


async def test_durable_epoch_queued_cancel_expiry_publish_cas_and_gc_fence(  # noqa: PLR0915 - keep successive epochs, durable stop proof and publication/GC fencing in one causal history
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
        assert current.deadline > utcnow() + timedelta(seconds=current.frozen.config.timeout_seconds)
    async with container.database.sessions.begin() as session:
        active_attempt = await session.get(GraphAttempt, current.attempt_id)
        active_attempt.deadline = utcnow() - timedelta(seconds=1)
    with pytest.raises(AppError) as expired:
        async with container.database.sessions.begin() as session:
            await container.graph_store.finish(
                session,
                current,
                status='completed',
                result={'chunks': 1, 'entities': 1, 'relations': 0, 'seconds': 0.0},
                error=None,
                stopped=True,
            )
    assert expired.value.code == 'graph_deadline_expired'
    async with container.database.sessions.begin() as session:
        active_attempt = await session.get(GraphAttempt, current.attempt_id)
        active_attempt.deadline = current.deadline
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
