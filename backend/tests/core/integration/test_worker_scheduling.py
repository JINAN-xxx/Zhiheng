from __future__ import annotations

import asyncio
from datetime import timedelta
from time import time
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import update

from tests.core.integration.test_chat_runs import admit
from tests.core.integration.test_knowledge_failures import ready_base, source
from zhiheng.infrastructure.worker_heartbeat import WORKER_HEARTBEAT_TTL_SECONDS, worker_heartbeat_age
from zhiheng.runtime.orm import Outbox
from zhiheng.shared.time import utcnow
from zhiheng.worker.main import build_worker

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx
    import pytest
    from fastapi import FastAPI

    from tests.core.provider import ProviderServer


async def wait_status(client: httpx.AsyncClient, path: str, expected: str, *, timeout: float = 6) -> dict:
    async with asyncio.timeout(timeout):
        while True:
            response = await client.get(path)
            assert response.status_code == 200, response.text
            value = response.json()
            if value['status'] == expected:
                return value
            assert value['status'] not in {'failed', 'cancelled', 'interrupted'}, value
            await asyncio.sleep(0.02)


async def import_document(client: httpx.AsyncClient, base_id: str) -> dict:
    file = await source(client, 'queued.txt', b'The service listens on port 8080.')
    response = await client.post(
        f'/api/knowledge/bases/{base_id}/documents',
        json={'file_ids': [file['id']], 'client_request_id': str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def query(client: httpx.AsyncClient, base_id: str) -> dict:
    response = await client.post(
        f'/api/knowledge/bases/{base_id}/query',
        json={'query': 'Which service port?', 'client_request_id': str(uuid4())},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def test_index_and_retrieval_progress_while_generation_slots_are_full(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    base = await ready_base(admin, app, provider)
    other = await admin.post(
        '/api/knowledge/bases', json={'name': 'Concurrent import', 'embed_model_id': base['embed_model_id']}
    )
    assert other.status_code == 201, other.text
    _, chat = await admit(admin, model_id)
    entered, release = asyncio.Event(), asyncio.Event()
    execute: Callable[[UUID], Awaitable[None]] = container.run_executor.execute

    async def slow_generation(run_id: UUID) -> None:
        if str(run_id) == chat['id']:
            entered.set()
            await release.wait()
        await execute(run_id)

    monkeypatch.setattr(container.run_executor, 'execute', slow_generation)
    worker = build_worker(container, max_jobs=1)
    running = asyncio.create_task(worker.async_run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        job = await import_document(admin, other.json()['id'])
        preparation = await query(admin, base['id'])
        completed, retrieved = await asyncio.gather(
            wait_status(admin, f'/api/knowledge/jobs/{job["id"]}', 'completed'),
            wait_status(admin, f'/api/knowledge/preparations/{preparation["id"]}', 'completed'),
        )
        assert completed['completed_documents'] == 1 and retrieved['hits']
        assert not release.is_set(), 'The occupied generation slot must remain blocked throughout the check'
    finally:
        release.set()
        await worker.close()
        await asyncio.gather(running, return_exceptions=True)


async def test_slow_builds_do_not_starve_retrieval(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    base = await ready_base(admin, app, provider)
    jobs = []
    for number in range(2):
        response = await admin.post(
            '/api/knowledge/bases', json={'name': f'Slow import {number}', 'embed_model_id': base['embed_model_id']}
        )
        assert response.status_code == 201, response.text
        jobs.append(await import_document(admin, response.json()['id']))
    entered, release = asyncio.Event(), asyncio.Event()
    seen: set[UUID] = set()
    tick: Callable[[UUID], Awaitable[None]] = container.knowledge_jobs.tick

    async def slow_build(job_id: UUID) -> None:
        if str(job_id) in {row['id'] for row in jobs}:
            seen.add(job_id)
            if len(seen) == 2:
                entered.set()
            await release.wait()
        await tick(job_id)

    monkeypatch.setattr(container.knowledge_jobs, 'tick', slow_build)
    worker = build_worker(container, max_jobs=2)
    running = asyncio.create_task(worker.async_run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        preparation = await query(admin, base['id'])
        result = await wait_status(admin, f'/api/knowledge/preparations/{preparation["id"]}', 'completed')
        assert result['hits'] and not release.is_set()
    finally:
        release.set()
        await worker.close()
        await asyncio.gather(running, return_exceptions=True)


async def test_blocked_reconciliation_does_not_stop_dispatch_or_heartbeat(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_reconcile() -> None:
        entered.set()
        await release.wait()

    monkeypatch.setattr(container.chat_context, 'reconcile', blocked_reconcile)
    worker = build_worker(container, max_jobs=1)
    running = asyncio.create_task(worker.async_run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        key = container.settings.worker_heartbeats_key
        async with asyncio.timeout(3):
            while not (heartbeats := await container.redis.zrange(key, 0, -1, withscores=True)):  # noqa: ASYNC110 - observe Redis across independent tasks
                await asyncio.sleep(0.02)
        ((member, before),) = heartbeats
        _, chat = await admit(admin, model_id)
        await wait_status(admin, f'/api/runs/{chat["id"]}', 'completed')
        async with asyncio.timeout(4):
            while (await container.redis.zscore(key, member) or 0) <= before:  # noqa: ASYNC110 - observe the independently maintained heartbeat
                await asyncio.sleep(0.02)
        heartbeat = await container.redis.zscore(key, member)
        assert heartbeat is not None
        assert 0 <= time() - heartbeat < 3 and not release.is_set()
    finally:
        release.set()
        await worker.close()
        await asyncio.gather(running, return_exceptions=True)


async def test_worker_exit_leaves_other_worker_heartbeats_and_execution_available(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = app.state.container
    key = container.settings.worker_heartbeats_key
    workers = [build_worker(container, max_jobs=1) for _ in range(4)]
    monkeypatch.setattr(type(workers[0]), '_install_signals', lambda _: None)
    tasks = [asyncio.create_task(worker.async_run()) for worker in workers]
    try:
        async with asyncio.timeout(5):
            while await container.redis.zcard(key) != 4:  # noqa: ASYNC110 - wait for all workers to publish through Redis
                await asyncio.sleep(0.02)
        assert (await admin.get('/health/ready')).status_code == 200
        await workers[0].close()
        await asyncio.gather(tasks[0], return_exceptions=True)
        assert await container.redis.zcard(key) == 3
        assert all(not task.done() for task in tasks[1:])
        assert (await admin.get('/health/ready')).status_code == 200
        monitor = await admin.get('/api/admin/monitor')
        assert monitor.status_code == 200
        assert monitor.json()['worker']['status'] == 'ok'
        _, chat = await admit(admin, model_id)
        await wait_status(admin, f'/api/runs/{chat["id"]}', 'completed')
    finally:
        await asyncio.gather(*(worker.close() for worker in workers))
        await asyncio.gather(*tasks, return_exceptions=True)
    assert await container.redis.zcard(key) == 0
    response = await admin.get('/health/ready')
    assert response.status_code == 503 and response.json()['component'] == 'worker'


async def test_health_filters_expired_workers_and_accepts_legacy_heartbeat(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    container = app.state.container
    key = container.settings.worker_heartbeats_key
    legacy = container.settings.worker_health_key
    now = time()
    await container.redis.zadd(key, {'expired:instance': now - WORKER_HEARTBEAT_TTL_SECONDS - 1, 'live:instance': now})
    assert await worker_heartbeat_age(container.redis, key, hostname='live') is not None
    assert await worker_heartbeat_age(container.redis, key, hostname='expired') is None
    assert (await admin.get('/health/ready')).status_code == 200
    await container.redis.zrem(key, 'live:instance')
    assert (await admin.get('/health/ready')).status_code == 503
    await container.redis.set(legacy, str(time()), ex=WORKER_HEARTBEAT_TTL_SECONDS)
    assert await worker_heartbeat_age(container.redis, key, hostname='live', legacy_key=legacy) is None
    assert (await admin.get('/health/ready')).status_code == 200


async def test_burst_waits_for_lost_delivery_inside_outbox_resend_cooldown(
    admin: httpx.AsyncClient,
    app: FastAPI,
    model_id: str,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = app.state.container
    _, run = await admit(admin, model_id)
    async with container.database.sessions.begin() as session:
        await session.execute(update(Outbox).where(Outbox.run_id == UUID(run['id'])).values(sent_at=utcnow()))
    third_maintenance = asyncio.Event()
    reconcile, passes = container.chat_context.reconcile, 0

    async def observed_reconcile() -> None:
        nonlocal passes
        await reconcile()
        passes += 1
        if passes == 3:
            third_maintenance.set()

    monkeypatch.setattr(container.chat_context, 'reconcile', observed_reconcile)
    worker = build_worker(container, burst=True)
    running = asyncio.create_task(worker.async_run())
    try:
        await asyncio.wait_for(third_maintenance.wait(), timeout=5)
        assert not running.done() and not provider.requests
        assert (await admin.get(f'/api/runs/{run["id"]}')).json()['status'] == 'pending'
        async with container.database.sessions.begin() as session:
            await session.execute(
                update(Outbox).where(Outbox.run_id == UUID(run['id'])).values(sent_at=utcnow() - timedelta(seconds=16))
            )
        await asyncio.wait_for(running, timeout=8)
        assert (await admin.get(f'/api/runs/{run["id"]}')).json()['status'] == 'completed'
        assert len(provider.requests) == 1
    finally:
        await worker.close()
        await asyncio.gather(running, return_exceptions=True)
