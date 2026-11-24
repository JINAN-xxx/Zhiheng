"""Opt-in load test over HTTP, Redis queues, PostgreSQL and a deterministic model."""

import asyncio
import json
import math
import os
from collections.abc import Iterator
from contextlib import AsyncExitStack, suppress
from socket import socket
from threading import Thread
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sqlalchemy import event

from tests.core.billing_support import enable_free_billing
from tests.core.permission_support import grants
from tests.core.workspace_provider import WorkspaceProvider
from zhiheng.bootstrap.container import Container
from zhiheng.bootstrap.settings import Settings
from zhiheng.runtime.contracts import TERMINAL_STATUSES
from zhiheng.worker.main import build_worker

pytestmark = pytest.mark.skipif(os.getenv('ZHIHENG_LOAD_TEST') != '1', reason='Opt-in isolated load test')


@pytest.fixture
def load_settings(settings: Settings) -> None:
    settings.provider_timeout_seconds = 30
    settings.lease_seconds = 30


@pytest.fixture
def load_provider() -> Iterator[WorkspaceProvider]:
    server = WorkspaceProvider()
    server.socket.listen(128)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _p95(values: list[float]) -> float | None:
    return round(sorted(values)[math.ceil(len(values) * 0.95) - 1] * 1_000, 1) if values else None


def _latencies(samples: list[dict[str, float | str]]) -> dict[str, object]:
    values = {
        field: _p95([float(sample[field]) for sample in samples if field in sample])
        for field in ('admission', 'active', 'first_text', 'complete')
    }
    return {
        'total': len(samples),
        'completed': sum(sample['status'] == 'completed' for sample in samples),
        'errors': [sample['status'] for sample in samples if sample['status'] != 'completed'],
        'p95_ms': values,
    }


async def _users(
    stack: AsyncExitStack, url: str, admin: httpx.AsyncClient, model_id: str
) -> list[tuple[httpx.AsyncClient, str, str]]:
    permissions = grants('platform:chat', 'use') | grants('platform:work', 'use', 'create')
    role = await admin.post('/api/admin/roles', json={'name': 'Load test', 'permissions': sorted(permissions)})
    role.raise_for_status()
    users = []
    for index in range(50):
        username = f'loadtest-{index + 1:03d}'
        password = uuid4().hex
        created = await admin.post(
            '/api/admin/users', json={'username': username, 'password': password, 'role_ids': [role.json()['id']]}
        )
        created.raise_for_status()
        user = await stack.enter_async_context(httpx.AsyncClient(base_url=url, timeout=120))
        (await user.post('/api/auth/login', json={'username': username, 'password': password})).raise_for_status()
        chat = await user.post('/api/chat/conversations', json={})
        chat.raise_for_status()
        task = await user.post('/api/work/tasks', json={'model_id': model_id, 'client_request_id': str(uuid4())})
        task.raise_for_status()
        users.append((user, chat.json()['id'], task.json()['id']))
    return users


async def _turn(user: httpx.AsyncClient, identifier: str, model_id: str, *, work: bool) -> dict[str, float | str]:
    measured: dict[str, float | str] = {'status': 'missing_terminal'}
    base = f'/api/work/tasks/{identifier}/runs' if work else f'/api/chat/conversations/{identifier}/runs'
    body: dict[str, Any] = {'client_request_id': str(uuid4()), 'message': '你好'}
    if work:
        task = await user.get(f'/api/work/tasks/{identifier}')
        task.raise_for_status()
        body['expected_version'] = task.json()['version']
    else:
        body['model_id'] = model_id
    started = perf_counter()
    response = await user.post(base, json=body)
    measured['admission'] = perf_counter() - started
    if response.status_code != 202:
        measured['status'] = f'admission:{response.status_code}:{response.text[:140]}'
        return measured
    run_id = response.json()['id']
    path = f'{base}/{run_id}/events' if work else f'/api/runs/{run_id}/events'
    async with user.stream('GET', path) as stream:
        stream.raise_for_status()
        async for line in stream.aiter_lines():
            if not line.startswith('data: '):
                continue
            run = json.loads(line[6:])
            elapsed = perf_counter() - started
            if run['status'] != 'pending':
                measured.setdefault('active', elapsed)
            if run['text']:
                measured.setdefault('first_text', elapsed)
            if run['status'] in TERMINAL_STATUSES:
                error = run.get('error')
                measured['status'] = error['code'] if error else run['status']
                measured['complete'] = elapsed
                break
    return measured


async def _scenario(
    users: list[tuple[httpx.AsyncClient, str, str]], model_id: str, count: int, scenario: str
) -> dict[str, object]:
    tasks = []
    for index, (user, chat_id, work_id) in enumerate(users[:count]):
        work = scenario == 'work' or (scenario == 'mixed' and index % 5 == 0)
        tasks.append(_turn(user, work_id if work else chat_id, model_id, work=work))
    return _latencies(await asyncio.wait_for(asyncio.gather(*tasks), timeout=150))


def _collect_database_metrics(
    container: Container, patch: pytest.MonkeyPatch, waits: list[float], reads: list[bool]
) -> None:
    engine = container.database.engine.sync_engine
    connect = engine.pool.connect

    def acquire() -> Any:
        started = perf_counter()
        try:
            return connect()
        finally:
            waits.append(perf_counter() - started)

    patch.setattr(engine.pool, 'connect', acquire)

    @event.listens_for(engine, 'before_cursor_execute')
    def count_reads(connection: Any, cursor: Any, statement: str, *args: Any) -> None:
        columns, _, remainder = statement.lower().partition('from runtime_runs')
        if columns.startswith('select') and remainder.startswith((' ', '\n')):
            reads.append('runtime_runs.payload' in columns)


async def test_50_users(  # noqa: PLR0915 - keep the HTTP server, worker and clients inside one isolated lifecycle
    load_settings: None,
    app: FastAPI,
    settings: Settings,
    load_provider: WorkspaceProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='warning', access_log=False))
    with socket() as listener:
        listener.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        serving = asyncio.create_task(api.serve(sockets=[listener]))
        worker_container = Container(settings)
        api_waits: list[float] = []
        worker_waits: list[float] = []
        reads: list[bool] = []
        _collect_database_metrics(app.state.container, monkeypatch, api_waits, reads)
        _collect_database_metrics(worker_container, monkeypatch, worker_waits, reads)
        worker = build_worker(worker_container)
        consuming = None
        try:
            async with AsyncExitStack() as stack:
                admin = await stack.enter_async_context(httpx.AsyncClient(base_url=url))
                while not api.started:
                    if serving.done():
                        await serving
                        pytest.fail('HTTP server terminated during startup')
                    await asyncio.sleep(0.02)
                (
                    await admin.post(
                        '/api/auth/login', json={'username': 'admin', 'password': 'platform-test-password'}
                    )
                ).raise_for_status()
                connection = await admin.post(
                    '/api/models/connections',
                    json={
                        'name': 'Load provider',
                        'base_url': f'http://127.0.0.1:{load_provider.server_port}/v1',
                        'api_key': 'load-test-only',
                    },
                )
                connection.raise_for_status()
                model = await admin.post(
                    '/api/models',
                    json={
                        'name': 'Load model',
                        'connection_id': connection.json()['id'],
                        'upstream_model': 'workspace-greeting',
                        'max_output_tokens': 64,
                    },
                )
                model.raise_for_status()
                model_id = model.json()['id']
                await enable_free_billing(admin)
                users = await _users(stack, url, admin, model_id)
                consuming = asyncio.create_task(worker.async_run())
                results = []
                for scenario in ('chat', 'work', 'mixed'):
                    for count in (5, 10, 25, 50):
                        api_waits.clear()
                        worker_waits.clear()
                        reads.clear()
                        result = await _scenario(users, model_id, count, scenario)
                        results.append(result)
                        result.update(
                            api_pool_acquire_p95_ms=_p95(api_waits),
                            worker_pool_acquire_p95_ms=_p95(worker_waits),
                            run_selects=len(reads),
                            run_selects_with_payload=sum(reads),
                        )
                        print(json.dumps({'scenario': scenario, 'users': count, **result}), flush=True)
                assert all(result['completed'] == result['total'] for result in results)
        finally:
            api.should_exit = True
            try:
                if consuming is not None:
                    await worker.close()
                    with suppress(asyncio.CancelledError):
                        await consuming
            finally:
                try:
                    await serving
                finally:
                    await worker_container.close()
