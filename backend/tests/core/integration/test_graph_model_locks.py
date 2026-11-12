"""Real PostgreSQL lock order and stale configuration fences, without SDK execution."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.core.graph_fixtures import evidence, graph_base
from tests.core.integration.test_knowledge_milvus import real_vectors  # noqa: F401
from tests.core.knowledge_support import legacy_grant, remove_library_member, share_library
from tests.core.provider import ProviderServer
from zhiheng.modules.knowledge.graph.contracts import GraphBuildRequest, GraphConfigPatch
from zhiheng.modules.knowledge.graph.orm import GraphAttempt, GraphConfiguration
from zhiheng.modules.knowledge.orm import KnowledgeBase
from zhiheng.modules.models.orm import ModelDefinition
from zhiheng.shared.errors import AppError


@pytest.mark.usefixtures('real_vectors')
@pytest.mark.parametrize('operation', ['freeze', 'patch', 'admit', 'renew', 'finish'])
async def test_graph_waits_for_models_without_owning_knowledge_base(  # noqa: PLR0915 - retain one lock lifetime
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, operation: str
) -> None:
    container = app.state.container
    base = await graph_base(admin, app, provider)
    base_id = UUID(base['id'])
    async with container.database.sessions.begin() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
        frozen = await container.graph.freeze(session, owner, base_id)
        claim = None
        if operation in {'renew', 'finish'}:
            job = await container.graph.admit(
                session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
            )
            claim = await container.graph_store.claim(session, job.id)
    if operation == 'finish':
        from zhiheng.modules.knowledge.public import GraphVectorManifest

        assert claim is not None
        manifest = GraphVectorManifest(
            base_id, claim.revision, claim.attempt_id, frozen.fingerprint, 2, claim.workspace
        )
        async with container.database.sessions.begin() as session:
            await container.graph_store.save_manifest(session, claim, manifest.dump())
    model_ids = sorted({frozen.config.llm_model_id, UUID(str(frozen.embedding_binding['model_id']))})
    started: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def contender() -> None:
        async with container.database.sessions.begin() as session:
            started.set_result(await session.scalar(text('SELECT pg_backend_pid()')))
            if operation == 'freeze':
                await container.graph.freeze(session, owner, base_id)
            elif operation == 'patch':
                await container.graph.patch(
                    session,
                    owner,
                    base_id,
                    GraphConfigPatch(expected_version=1, llm_model_id=frozen.config.llm_model_id),
                )
            elif operation == 'admit':
                await container.graph.admit(
                    session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
                )
            elif operation == 'renew':
                assert claim is not None
                await container.graph_store.renew(session, claim)
            else:
                assert claim is not None
                await container.graph_store.finish(
                    session,
                    claim,
                    status='completed',
                    result={'chunks': 1, 'entities': 1, 'relations': 0, 'seconds': 0.0},
                    error=None,
                    stopped=True,
                )

    async with container.database.sessions() as holder, holder.begin():
        holder_pid = await holder.scalar(text('SELECT pg_backend_pid()'))
        await holder.execute(
            select(ModelDefinition.id)
            .where(ModelDefinition.id.in_(model_ids))
            .order_by(ModelDefinition.id)
            .with_for_update(key_share=True)
        )
        task = asyncio.create_task(contender())
        try:
            contender_pid = await asyncio.wait_for(started, timeout=5)
            blocked = False
            for _ in range(200):
                blocked = bool(
                    await holder.scalar(
                        text('SELECT CAST(:holder AS int)=ANY(pg_blocking_pids(CAST(:contender AS int)))'),
                        {'holder': holder_pid, 'contender': contender_pid},
                    )
                )
                if blocked or task.done():
                    break
                await asyncio.sleep(0.01)
            assert blocked, 'Contender must reach a real model lock wait'
            kb_available = False
            try:
                async with holder.begin_nested():
                    await holder.execute(
                        select(KnowledgeBase.id).where(KnowledgeBase.id == base_id).with_for_update(nowait=True)
                    )
                    kb_available = True
            except DBAPIError as error:
                assert getattr(error.orig, 'sqlstate', None) == '55P03'
            evidence('model-order-' + operation, {'blocked_on_models': blocked, 'holder_can_lock_kb': kb_available})
        finally:
            await holder.rollback()
            await asyncio.wait_for(task, timeout=8)
    assert kb_available, 'Graph acquired the knowledge-base lock before waiting for its model dependencies'
    if operation == 'finish':
        assert claim is not None
        async with container.database.sessions() as session:
            config = await session.get(GraphConfiguration, base_id)
            attempt = await session.get(GraphAttempt, claim.attempt_id)
            assert config.active_revision == claim.revision and attempt.writer_stopped


@pytest.mark.usefixtures('real_vectors')
async def test_freeze_rechecks_configuration_after_models_before_locking_base(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    base = await graph_base(admin, app, provider)
    base_id = UUID(base['id'])
    async with container.database.sessions() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
    paused, release = asyncio.Event(), asyncio.Event()
    require = container.knowledge.require
    task: asyncio.Task[Any] | None = None

    async def pause_before_base_lock(session: Any, principal: Any, selected: UUID, **kwargs: Any) -> Any:
        if asyncio.current_task() is task and kwargs.get('lock') and not paused.is_set():
            paused.set()
            await release.wait()
        return await require(session, principal, selected, **kwargs)

    monkeypatch.setattr(container.knowledge, 'require', pause_before_base_lock)

    async def freeze() -> Any:
        async with container.database.sessions.begin() as session:
            return await container.graph.freeze(session, owner, base_id)

    task = asyncio.create_task(freeze())
    try:
        await asyncio.wait_for(paused.wait(), timeout=5)
        updated = await admin.patch(
            f'/api/knowledge/bases/{base_id}/graph/config',
            json={'expected_version': 1, 'llm_model_id': model_id, 'max_output_tokens': 2048},
        )
        assert updated.status_code == 200, updated.text
        release.set()
        with pytest.raises(AppError) as rejected:
            await asyncio.wait_for(task, timeout=5)
        assert rejected.value.code == 'graph_stale'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.usefixtures('real_vectors')
async def test_owned_rejects_changed_model_selection_without_locking_new_model(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, model_id: str
) -> None:
    container = app.state.container
    base = await graph_base(admin, app, provider)
    base_id = UUID(base['id'])
    async with container.database.sessions.begin() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
        job = await container.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )
        claim = await container.graph_store.claim(session, job.id)
    updated = await admin.patch(
        f'/api/knowledge/bases/{base_id}/graph/config',
        json={'expected_version': 1, 'llm_model_id': model_id, 'max_output_tokens': 2048},
    )
    assert updated.status_code == 200, updated.text

    async def renew() -> None:
        async with container.database.sessions.begin() as session:
            await container.graph_store.renew(session, claim)

    async with container.database.sessions.begin() as holder:
        await holder.execute(select(ModelDefinition.id).where(ModelDefinition.id == UUID(model_id)).with_for_update())
        task = asyncio.create_task(renew())
        try:
            done, _ = await asyncio.wait({task}, timeout=0.5)
            completed_without_new_model_lock = bool(done)
        finally:
            await holder.rollback()
        with pytest.raises(AppError) as rejected:
            await asyncio.wait_for(task, timeout=5)
        assert rejected.value.code == 'graph_stale'
    assert completed_without_new_model_lock, 'Frozen ownership must reject a changed model before locking its new row'


@pytest.mark.usefixtures('real_vectors')
@pytest.mark.parametrize('lost_epoch', [False, True])
async def test_finish_preserves_actual_stop_proof_after_model_disable(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, lost_epoch: bool
) -> None:
    container = app.state.container
    base = await graph_base(admin, app, provider)
    base_id = UUID(base['id'])
    async with container.database.sessions.begin() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
        job = await container.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )
        claim = await container.graph_store.claim(session, job.id)
    disabled = await admin.patch('/api/models/' + str(claim.frozen.config.llm_model_id), json={'enabled': False})
    assert disabled.status_code == 200, disabled.text
    if lost_epoch:
        async with container.database.sessions.begin() as session:
            attempt = await session.get(GraphAttempt, claim.attempt_id)
            attempt.epoch += 1
            attempt.status = 'interrupted'
    async with container.database.sessions.begin() as session:
        if not lost_epoch:
            with pytest.raises(AppError):
                await container.graph_store.finish(
                    session, claim, status='completed', result=None, error=None, stopped=True
                )
        await container.graph_store.finish(
            session,
            claim,
            status='failed',
            result=None,
            error={'code': 'test_stopped', 'message': 'Original task drained'},
            stopped=True,
        )
    async with container.database.sessions() as session:
        attempt = await session.get(GraphAttempt, claim.attempt_id)
        config = await session.get(GraphConfiguration, base_id)
        assert attempt.writer_stopped and attempt.stopped_at is not None
        assert attempt.status == ('interrupted' if lost_epoch else 'failed')
        assert config.active_revision is None
    cancelled = await admin.post(f'/api/knowledge/bases/{base_id}/graph/jobs/{job.id}/cancel', json={})
    assert cancelled.status_code == 200, cancelled.text


@pytest.mark.usefixtures('real_vectors')
async def test_pin_rechecks_active_generation_after_waiting_for_its_fence(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zhiheng.modules.knowledge.graph.lifecycle import retire_base_graph
    from zhiheng.modules.knowledge.graph.orm import GraphPin
    from zhiheng.modules.knowledge.public import GraphVectorManifest

    container = app.state.container
    base = await graph_base(admin, app, provider)
    base_id = UUID(base['id'])
    async with container.database.sessions.begin() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
        job = await container.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )
        claim = await container.graph_store.claim(session, job.id)
        manifest = GraphVectorManifest(
            base_id, claim.revision, claim.attempt_id, claim.frozen.fingerprint, 2, claim.workspace
        )
        await container.graph_store.save_manifest(session, claim, manifest.dump())
        await container.graph_store.finish(
            session,
            claim,
            status='completed',
            result={'chunks': 1, 'entities': 1, 'relations': 0, 'seconds': 0.0},
            error=None,
            stopped=True,
        )
    paused, release = asyncio.Event(), asyncio.Event()
    authorize = container.graph.authorize
    task: asyncio.Task[Any] | None = None

    async def pause_fence(session: Any, principal: Any, selected: UUID, frozen: Any) -> None:
        if asyncio.current_task() is task and not paused.is_set():
            paused.set()
            await release.wait()
        await authorize(session, principal, selected, frozen)

    monkeypatch.setattr(container.graph, 'authorize', pause_fence)

    async def pin() -> Any:
        async with container.database.sessions.begin() as session:
            return await container.graph_store.pin_read(session, owner, base_id)

    task = asyncio.create_task(pin())
    try:
        await asyncio.wait_for(paused.wait(), timeout=5)
        async with container.database.sessions.begin() as session:
            await session.execute(select(KnowledgeBase.id).where(KnowledgeBase.id == base_id).with_for_update())
            await retire_base_graph(session, base_id)
        release.set()
        with pytest.raises(AppError) as rejected:
            await asyncio.wait_for(task, timeout=5)
        assert rejected.value.code == 'graph_stale'
        async with container.database.sessions() as session:
            assert list(await session.scalars(select(GraphPin.id))) == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.usefixtures('real_vectors')
@pytest.mark.parametrize('transition', ['cancel', 'epoch'])
async def test_callback_guard_refreshes_fence_after_waiting_for_models(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, transition: str
) -> None:
    container = app.state.container
    base = await graph_base(admin, app, provider)
    base_id = UUID(base['id'])
    async with container.database.sessions.begin() as session:
        owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
        job = await container.graph.admit(
            session, owner, base_id, GraphBuildRequest(expected_version=1, client_request_id=uuid4()), 'build'
        )
        claim = await container.graph_store.claim(session, job.id)
        run = await container.runs.create(
            session,
            owner_id=owner.id,
            resource_type='knowledge_graph',
            resource_id=claim.attempt_id,
            request_id=uuid4(),
            fingerprint='1' * 64,
            payload={},
        )
        await container.graph_store.add_step(
            session, claim, role='extract', operation='generation', ordinal=1, run_id=run.id, fingerprint='1' * 64
        )
    model_ids = sorted({claim.frozen.config.llm_model_id, UUID(str(claim.frozen.embedding_binding['model_id']))})
    started: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def guard() -> None:
        async with container.database.sessions.begin() as session:
            started.set_result(await session.scalar(text('SELECT pg_backend_pid()')))
            await container.graph_store.guard(session, run.id, claim.attempt_id)

    async with container.database.sessions.begin() as holder:
        holder_pid = await holder.scalar(text('SELECT pg_backend_pid()'))
        await holder.execute(
            select(ModelDefinition.id)
            .where(ModelDefinition.id.in_(model_ids))
            .order_by(ModelDefinition.id)
            .with_for_update(key_share=True)
        )
        task = asyncio.create_task(guard())
        try:
            contender_pid = await asyncio.wait_for(started, timeout=5)
            blocked = False
            for _ in range(200):
                blocked = bool(
                    await holder.scalar(
                        text('SELECT CAST(:holder AS int)=ANY(pg_blocking_pids(CAST(:contender AS int)))'),
                        {'holder': holder_pid, 'contender': contender_pid},
                    )
                )
                if blocked:
                    break
                await asyncio.sleep(0.01)
            assert blocked
            if transition == 'cancel':
                cancelled = await container.graph.cancel(holder, owner, base_id, job.id)
                assert cancelled.status == 'draining'
            else:
                from datetime import timedelta

                from zhiheng.shared.time import utcnow

                attempt = await holder.get(GraphAttempt, claim.attempt_id)
                attempt.lease_until = utcnow() - timedelta(seconds=1)
                await container.graph_store.recover(holder)
            await holder.commit()
            with pytest.raises(AppError) as rejected:
                await asyncio.wait_for(task, timeout=5)
            assert rejected.value.code == ('graph_cancelled' if transition == 'cancel' else 'graph_execution_lost')
        finally:
            await holder.rollback()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.usefixtures('real_vectors')
@pytest.mark.parametrize('revocation', ['deny', 'delete'])
async def test_freeze_refreshes_cached_base_grant_after_waiting_for_models(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, revocation: str
) -> None:
    from tests.core.integration.test_work import member_client
    from zhiheng.modules.knowledge.orm import KnowledgeGrant

    container = app.state.container
    base = await graph_base(admin, app, provider, legacy=revocation == 'deny')
    base_id = UUID(base['id'])
    async with member_client(app) as member:
        member_id = UUID((await member.get('/api/auth/me')).json()['id'])
        endpoint = f'/api/knowledge/bases/{base_id}'
        await (
            legacy_grant(app, base['id'], str(member_id))
            if revocation == 'deny'
            else share_library(admin, member, base['id'])
        )
        document = (await admin.get(endpoint + '/documents')).json()['items'][0]
        allowed = await admin.patch(
            f'/api/files/{document["file_id"]}/acl',
            json={
                'expected_version': 1,
                'access': 'private',
                'entries': [{'user_id': str(member_id), 'effect': 'allow'}],
            },
        )
        assert allowed.status_code == 200, allowed.text
        async with container.database.sessions.begin() as session:
            owner = await container.identity.principal_for_user(session, UUID(base['owner_id']))
            reader = await container.identity.principal_for_user(session, member_id)
            frozen = await container.graph.freeze(session, owner, base_id)
        ids = sorted({frozen.config.llm_model_id, UUID(str(frozen.embedding_binding['model_id']))})
        started: asyncio.Future[int] = asyncio.get_running_loop().create_future()

        async def freeze_with_cached_grant() -> None:
            async with container.database.sessions.begin() as session:
                cached = await session.get(KnowledgeGrant, (base_id, member_id))
                assert cached.effect == 'allow'
                started.set_result(await session.scalar(text('SELECT pg_backend_pid()')))
                await container.graph.freeze(session, reader, base_id)
                assert cached.effect == 'allow'

        async with container.database.sessions.begin() as holder:
            pid = await holder.scalar(text('SELECT pg_backend_pid()'))
            await holder.execute(
                select(ModelDefinition.id)
                .where(ModelDefinition.id.in_(ids))
                .order_by(ModelDefinition.id)
                .with_for_update(key_share=True)
            )
            task = asyncio.create_task(freeze_with_cached_grant())
            try:
                other = await asyncio.wait_for(started, timeout=5)
                blocked = False
                for _ in range(200):
                    blocked = bool(
                        await holder.scalar(
                            text('SELECT CAST(:holder AS int)=ANY(pg_blocking_pids(CAST(:contender AS int)))'),
                            {'holder': pid, 'contender': other},
                        )
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
                assert blocked
                await (
                    legacy_grant(app, base['id'], str(member_id), effect='deny')
                    if revocation == 'deny'
                    else remove_library_member(admin, member, base['id'])
                )
                await holder.rollback()
                with pytest.raises(AppError) as rejected:
                    await asyncio.wait_for(task, timeout=5)
                assert rejected.value.code == 'knowledge_not_found'
            finally:
                await holder.rollback()
                await asyncio.gather(task, return_exceptions=True)
