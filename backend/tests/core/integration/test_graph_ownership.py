import asyncio
import errno
import os
import threading
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from pymilvus import MilvusClient
from sqlalchemy import select, text

from tests.core.graph_fixtures import empty_frozen, evidence, graph_base, graph_role
from tests.core.integration.test_knowledge_milvus import real_vectors  # noqa: F401
from tests.core.resources import resource
from zhiheng.bootstrap.migrations import migrate
from zhiheng.modules.developer.orm import Invocation
from zhiheng.modules.knowledge.adapters.lightrag import sdk as boundary
from zhiheng.modules.knowledge.adapters.lightrag.factory import shutdown_rag
from zhiheng.modules.knowledge.adapters.lightrag.owned_loop import _CURRENT_TASKS
from zhiheng.modules.knowledge.adapters.lightrag.postgres import CURRENT_STORAGE, PostgresContext
from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC
from zhiheng.modules.knowledge.adapters.lightrag.vector import CURRENT_VECTOR, GraphVectorManifest
from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl
from zhiheng.modules.knowledge.graph.orm import GraphAttempt, GraphGeneration, GraphStep
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow
from zhiheng.worker.main import build_worker


@pytest.mark.parametrize(
    'stage',
    [
        'workroot',
        'temporary',
        'constructor',
        'partial-constructor',
        'catalog',
        pytest.param(
            'partial-read',
            marks=pytest.mark.skipif(not os.getenv('ZHIHENG_TEST_MILVUS_URI'), reason='Separate Milvus URI required'),
        ),
    ],
)
async def test_initialization_releases_each_acquisition_and_proves_actual_stop(settings, tmp_path, monkeypatch, stage):
    await migrate(settings)
    frozen = empty_frozen()
    contexts, rpcs, calls, scopes, stopped = [], [], [], [], []
    original_context, original_rpc, original_submit = (
        PostgresContext.__init__,
        GraphVectorRPC.__init__,
        GraphVectorRPC._submit,
    )

    def context_init(owner, *args, **kwargs):
        original_context(owner, *args, **kwargs)
        contexts.append(owner)

    def rpc_init(owner, *args, **kwargs):
        original_rpc(owner, *args, **kwargs)
        rpcs.append(owner)

    def submit(owner, name, args, kwargs):
        calls.append(name)
        return original_submit(owner, name, args, kwargs)

    monkeypatch.setattr(PostgresContext, '__init__', context_init)
    monkeypatch.setattr(GraphVectorRPC, '__init__', rpc_init)
    monkeypatch.setattr(GraphVectorRPC, '_submit', submit)
    async with graph_role(settings) as (pg, url, role):
        root = tmp_path / 'scratch'
        if stage == 'workroot':
            root.write_text('Actual regular file, not a mocked mkdir.')
        instance = boundary.GraphSDK(
            database_url=url,
            # Initialization faults must stop before vector IO (asserted below).
            # Only partial-read requires a configured, real Milvus connection.
            milvus_uri=os.getenv('ZHIHENG_TEST_MILVUS_URI', 'http://127.0.0.1:1'),
            milvus_token='',
            milvus_database='default',
            working_root=root,
        )
        original_run = instance.owned_loop.run

        async def observed_run(program):
            async def observed():
                before = (CURRENT_STORAGE.get(None), CURRENT_VECTOR.get(None), _CURRENT_TASKS.get())
                try:
                    return await program()
                finally:
                    after = (CURRENT_STORAGE.get(None), CURRENT_VECTOR.get(None), _CURRENT_TASKS.get())
                    scopes.append([left is right for left, right in zip(before, after)])

            return await original_run(observed)

        monkeypatch.setattr(instance.owned_loop, 'run', observed_run)
        expected = AppError
        if stage == 'workroot':
            expected = FileExistsError
        elif stage == 'temporary':

            def enospc(*args, **kwargs):
                raise OSError(errno.ENOSPC, 'Explicit temporary-directory fault injection')

            monkeypatch.setattr(boundary, 'TemporaryDirectory', enospc)
            expected = OSError
        elif stage in {'constructor', 'partial-constructor'}:
            create = boundary.create_rag

            def constructor(**kwargs):
                if stage == 'partial-constructor':
                    create(**kwargs)
                raise AppError('test_constructor_fault', 'Explicit constructor fault injection', 503)

            monkeypatch.setattr(boundary, 'create_rag', constructor)
        elif stage == 'catalog':
            await pg.execute(f'REVOKE UPDATE ON knowledge_graph_sdk.lightrag_doc_full FROM "{role}"')

        async def no_model(call):
            raise AssertionError('Initialization must precede model work')

        facts = []
        try:
            for iteration in range(3 if stage == 'workroot' else 1):
                attempt = uuid4()
                manifest = GraphVectorManifest(
                    frozen.base_id, uuid4(), attempt, frozen.fingerprint, 2, 'g_' + attempt.hex
                )
                with pytest.raises(expected):
                    await instance.execute(
                        operation='labels' if stage == 'partial-read' else 'build',
                        frozen=frozen,
                        manifest=manifest,
                        request={},
                        model=no_model,
                        control=VectorIOControl(timeout_seconds=15),
                        stopped=lambda: stopped.append(True),
                    )
                sessions = [
                    dict(row) for row in await pg.fetch('SELECT pid,state FROM pg_stat_activity WHERE usename=$1', role)
                ]
                facts.append({'iteration': iteration, 'pg_sessions': sessions})
            evidence(
                'initialization-' + stage,
                {
                    'facts': facts,
                    'tokens_restored': scopes,
                    'stopped_callbacks': len(stopped),
                    'contexts': [{'pool_none': c.db.pool is None, 'leases': len(c.leases)} for c in contexts],
                    'rpcs': [
                        {'stopped': rpc.stopped, 'pending': rpc.pending_count, 'threads': rpc.live_threads}
                        for rpc in rpcs
                    ],
                    'rpc_calls': calls,
                    'fault_injection': stage in {'temporary', 'constructor', 'partial-constructor'},
                },
            )
            assert not any(fact['pg_sessions'] for fact in facts), 'Acquired real PG pool was abandoned'
            assert all(all(scope) for scope in scopes), 'Attempt tokens were not reset in their original task'
            assert all(c.db.pool is None and not c.leases for c in contexts)
            assert all(rpc.stopped and rpc.pending_count == rpc.live_threads == 0 for rpc in rpcs)
            assert len(stopped) == len(facts), 'Actual unstarted/drained attempt has no stop proof'
            assert not calls if stage != 'partial-read' else calls == ['has_collection']
        finally:
            # Exact original-loop fallback is only fixture cleanup after red evidence.
            async def finish():
                for rpc in rpcs:
                    await rpc.aclose()
                for context in contexts:
                    await context.close()

            await original_run(finish)
            await instance.close()


@pytest.mark.parametrize(
    'failure',
    ['workroot', 'manifest', 'pre-sdk-cancel', 'cancel-during-cleanup', 'cancel-during-finish', 'cooperative-shutdown'],
)
async def test_public_failed_attempt_closes_resources_preserves_fees_and_can_gc(
    admin, app, provider, real_vectors, tmp_path, monkeypatch, failure
):
    container = app.state.container
    base = await graph_base(admin, app, provider)
    path = f'/api/knowledge/bases/{base["id"]}/graph'
    captures, calls, collections = [], [], {}
    worker = worker_task = None
    release_cleanup = threading.Event()
    entered, observer_done = asyncio.Event(), asyncio.Event()
    release_finish = asyncio.Event()
    pending_finish = {}
    driver = None
    client = MilvusClient(uri=os.environ['ZHIHENG_TEST_MILVUS_URI'], timeout=8)
    async with graph_role(container.settings) as (pg, url, role):
        container.graph_sdk.database_url, container.graph_sdk.uri = url, os.environ['ZHIHENG_TEST_MILVUS_URI']
        if failure in {'workroot', 'cancel-during-cleanup', 'cancel-during-finish'}:
            occupied = tmp_path / 'occupied'
            occupied.write_text('Actual configured filesystem failure')
            container.graph_sdk.root = occupied
        elif failure == 'manifest':

            async def fail_manifest(*args, **kwargs):
                raise AppError('test_manifest_fault', 'Explicit failure before SDK entry', 503)

            monkeypatch.setattr(container.graph_store, 'save_manifest', fail_manifest)
        elif failure == 'pre-sdk-cancel':
            execute = container.graph_jobs._execute

            async def before_sdk(*args, **kwargs):
                entered.set()
                await asyncio.Event().wait()
                return await execute(*args, **kwargs)

            monkeypatch.setattr(container.graph_jobs, '_execute', before_sdk)
        else:
            create = boundary.create_rag

            def capture(**kwargs):
                rag = create(**kwargs)
                first = rag.role_llm_funcs['extract']
                shutdown = first.shutdown

                async def fail_after_real_shutdown(*args, **kwargs):
                    await shutdown(*args, **kwargs)
                    raise AppError('test_queue_shutdown', 'Explicit error after cooperative shutdown', 503)

                first.shutdown = fail_after_real_shutdown
                captures.append((rag, _CURRENT_TASKS.get(), CURRENT_STORAGE.get(), first, shutdown))
                return rag

            monkeypatch.setattr(boundary, 'create_rag', capture)
        response = await admin.post(path + '/build', json={'expected_version': 1, 'client_request_id': str(uuid4())})
        assert response.status_code == 202, response.text
        job = response.json()
        async with container.database.sessions() as session:
            generation = await session.get(GraphGeneration, UUID(job['graph_revision']))
            attempt = await session.get(GraphAttempt, UUID(job['attempt_id']))
            manifest = GraphVectorManifest(
                UUID(base['id']), generation.id, attempt.id, generation.fingerprint, 2, attempt.workspace
            )
        for vector_role in ('entities', 'relationships', 'chunks'):
            name, description = manifest.collection(vector_role), manifest.description(vector_role)
            collections[name] = description
            resource('intent', 'milvus_collection', name, description=description, database='default')
        submit = GraphVectorRPC._submit

        def observed_submit(owner, name, args, kwargs):
            calls.append(name)
            return submit(owner, name, args, kwargs)

        monkeypatch.setattr(GraphVectorRPC, '_submit', observed_submit)
        if failure == 'cancel-during-cleanup':
            loop = asyncio.get_running_loop()
            close = PostgresContext.close
            observe = container.graph_jobs._observe

            async def held_close(owner):
                loop.call_soon_threadsafe(entered.set)
                await asyncio.to_thread(release_cleanup.wait)
                await close(owner)

            async def observe_stop(*args):
                try:
                    await observe(*args)
                finally:
                    observer_done.set()

            monkeypatch.setattr(PostgresContext, 'close', held_close)
            monkeypatch.setattr(container.graph_jobs, '_observe', observe_stop)
        elif failure == 'cancel-during-finish':
            finish = container.graph_store.finish

            async def held_finish(session, *args, **kwargs):
                await finish(session, *args, **kwargs)
                await session.flush()
                pending_finish['pid'] = await session.scalar(text('SELECT pg_backend_pid()'))
                pending_finish['writer_stopped'] = (
                    await session.get(GraphAttempt, UUID(job['attempt_id']))
                ).writer_stopped
                entered.set()
                await release_finish.wait()

            monkeypatch.setattr(container.graph_store, 'finish', held_finish)
        before = len(provider.requests)
        try:
            if failure in {'workroot', 'manifest'}:
                await container.graph_jobs.execute(UUID(job['id']))
            elif failure in {'pre-sdk-cancel', 'cancel-during-cleanup', 'cancel-during-finish'}:
                driver = asyncio.create_task(container.graph_jobs.execute(UUID(job['id'])))
                await asyncio.wait_for(entered.wait(), timeout=10)
                driver.cancel()
                if failure == 'cancel-during-finish':
                    done, _ = await asyncio.wait({driver}, timeout=0.1)
                    recovered = None
                    if driver in done:
                        # Existing recovery has no durable stop proof after a rolled-back finish.
                        async with container.database.sessions.begin() as session:
                            lost = await session.get(GraphAttempt, UUID(job['attempt_id']))
                            lost.lease_until = utcnow() - timedelta(seconds=1)
                        async with container.database.sessions.begin() as session:
                            await container.graph_store.recover(session)
                        async with container.database.sessions() as session:
                            lost = await session.get(GraphAttempt, UUID(job['attempt_id']))
                            recovered = {'status': lost.status, 'writer_stopped': lost.writer_stopped}
                    evidence(
                        'cancel-before-finish-commit',
                        {
                            'driver_done': driver in done,
                            'real_pending_transaction': pending_finish,
                            'recovered_after_only_expiring_lease': recovered,
                            'actual_sdk_role_backends': [
                                dict(row)
                                for row in await pg.fetch(
                                    'SELECT pid,state FROM pg_stat_activity WHERE usename=$1', role
                                )
                            ],
                            'fault_injection': 'Original finish and actual SQL flush completed; only pause before transaction commit',
                        },
                    )
                    assert driver not in done, 'Cancellation rolled back the durable stop proof after actual drain'
                    release_finish.set()
                elif failure == 'cancel-during-cleanup':
                    await asyncio.wait_for(observer_done.wait(), timeout=5)
                    driver.cancel()
                    await asyncio.sleep(0)
                    await asyncio.sleep(0)
                    premature = driver.done()
                    evidence(
                        'repeated-cancel-before-release',
                        {
                            'driver_done': premature,
                            'actual_pg_backends': [
                                dict(row)
                                for row in await pg.fetch(
                                    'SELECT pid,state FROM pg_stat_activity WHERE usename=$1', role
                                )
                            ],
                            'fault_injection': 'Only hold real pool close; second cancellation after observer actually stopped',
                        },
                    )
                    assert not premature, 'Repeated cancellation abandoned the still-owned SDK cleanup'
                    release_cleanup.set()
                await driver
            else:
                worker = build_worker(container, max_jobs=1)
                worker_task = asyncio.create_task(worker.async_run())

                async def terminal():
                    while (await admin.get(path + '/jobs/' + job['id'])).json()['status'] not in {
                        'failed',
                        'completed',
                    }:
                        await asyncio.sleep(0.02)

                await asyncio.wait_for(terminal(), timeout=90)
            job = (await admin.get(path + '/jobs/' + job['id'])).json()
            async with container.database.sessions.begin() as session:
                attempt = await session.get(GraphAttempt, UUID(job['attempt_id']))
                generation = await session.get(GraphGeneration, UUID(job['graph_revision']))
                generation.gc_after = utcnow() - timedelta(seconds=1)
                gc = await container.graph_store.claim_gc(session, generation.id)
                steps = list(await session.scalars(select(GraphStep).where(GraphStep.attempt_id == attempt.id)))
                runs = list(await session.scalars(select(Run).where(Run.resource_id == attempt.id)))
                invoices = list(
                    await session.scalars(select(Invocation).where(Invocation.run_id.in_([r.id for r in runs])))
                )
            sessions = [
                dict(row) for row in await pg.fetch('SELECT pid,state FROM pg_stat_activity WHERE usename=$1', role)
            ]
            evidence(
                'public-owner-' + failure,
                {
                    'job': job,
                    'writer_stopped': attempt.writer_stopped,
                    'stopped_at': attempt.stopped_at,
                    'claim_gc': gc,
                    'sessions': sessions,
                    'http_delta': len(provider.requests) - before,
                    'rpc_calls': calls,
                    'paid_runs': [{'id': r.id, 'status': r.status, 'attempt_no': r.attempt_no} for r in runs],
                    'invocations': [{'status': r.status, 'cost': r.billable_cost} for r in invoices],
                },
            )
            assert job['status'] == (
                'interrupted' if failure in {'pre-sdk-cancel', 'cancel-during-cleanup'} else 'failed'
            )
            expected_code = {
                'workroot': 'graph_failed',
                'manifest': 'test_manifest_fault',
                'pre-sdk-cancel': 'graph_worker_stopped',
                'cancel-during-cleanup': 'graph_worker_stopped',
                'cooperative-shutdown': 'test_queue_shutdown',
            }
            expected_code['cancel-during-finish'] = 'graph_failed'
            assert job['error']['code'] == expected_code[failure]
            assert not sessions
            assert attempt.writer_stopped and attempt.stopped_at is not None
            assert gc == ({} if failure in {'manifest', 'pre-sdk-cancel'} else manifest.dump())
            if failure != 'cooperative-shutdown':
                assert not calls and not steps and len(provider.requests) == before
            else:
                assert runs and len(runs) == len(steps) == len(invoices) == len(provider.requests) - before
                assert all(r.status == 'completed' and r.attempt_no == 1 for r in runs)
                assert all(r.status == 'known' and r.billable_cost > 0 for r in invoices)
                for rag, owned, context, _, _ in captures:
                    assert not context.leases and context.db.pool is None
                    assert all(t.done() for t in owned)
                    assert all(getattr(rag, n).rpc.stopped for n in ('entities_vdb', 'relationships_vdb', 'chunks_vdb'))
        finally:
            release_cleanup.set()
            release_finish.set()
            if driver is not None:
                await asyncio.gather(driver, return_exceptions=True)
            for rag, owned, context, first, shutdown in captures:
                first.shutdown = shutdown

                async def finish():
                    try:
                        await shutdown_rag(rag, owned)
                    except AppError:
                        pass
                    finally:
                        await context.close()

                await container.graph_sdk.owned_loop.run(finish)
            provider.release.set()
            if worker_task is not None:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
            if worker is not None:
                await worker.close()
            # Any leaked pool in the real-filesystem red is closed on its original loop.
            await container.graph_sdk.close()
            for row in await pg.fetch('SELECT pid FROM pg_stat_activity WHERE usename=$1', role):
                await pg.fetchval('SELECT pg_terminate_backend($1)', row['pid'])
            for name, description in collections.items():
                if client.has_collection(name):
                    assert client.describe_collection(name)['description'] == description
                    client.drop_collection(name)
                resource('deleted', 'milvus_collection', name, database='default')
            client.close()
