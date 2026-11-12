import asyncio
import socket
from collections.abc import AsyncIterator
from time import monotonic
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
import uvicorn
from sqlalchemy import text

from tests.core.integration.test_chat_runs import admit


@pytest_asyncio.fixture
async def tcp_server(app: object) -> AsyncIterator[tuple[uvicorn.Server, str]]:
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error'))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        deadline = monotonic() + 3
        while not server.started:
            assert monotonic() < deadline
            await asyncio.sleep(0.01)
        yield server, f'http://127.0.0.1:{listener.getsockname()[1]}'
    finally:
        server.should_exit = True
        for task in list(server.server_state.tasks):
            task.cancel()
        await asyncio.gather(*server.server_state.tasks, return_exceptions=True)
        try:
            await asyncio.wait_for(serving, timeout=2)
        except TimeoutError:
            serving.cancel()
            await asyncio.gather(serving, return_exceptions=True)
        listener.close()


@pytest.mark.parametrize('revocation', ['logout', 'expiry', 'disabled'])
async def test_sse_stops_after_its_session_is_revoked(
    admin: httpx.AsyncClient,
    model_id: str,
    app: object,
    tcp_server: tuple[uvicorn.Server, str],
    revocation: str,
) -> None:
    _conversation, run = await admit(admin, model_id)
    _server, base_url = tcp_server
    token = admin.cookies.get('zhiheng_session')
    async with (
        httpx.AsyncClient(
            base_url=base_url, trust_env=False, timeout=2, headers={'Cookie': f'zhiheng_session={token}'}
        ) as client,
        client.stream('GET', f'/api/runs/{run["id"]}/events') as response,
    ):
        assert response.status_code == 200
        lines = response.aiter_lines()
        while not (await anext(lines)).startswith('data:'):
            pass
        if revocation == 'logout':
            assert (await admin.post('/api/auth/logout', json={})).status_code == 204
        else:
            async with app.state.container.database.sessions.begin() as session:
                statement = (
                    "UPDATE identity_sessions SET expires_at=now()-interval '1 second'"
                    if revocation == 'expiry'
                    else "UPDATE identity_users SET is_active=false WHERE username='admin'"
                )
                await session.execute(text(statement))
        async with app.state.container.database.sessions.begin() as session:
            await session.execute(
                text('UPDATE runtime_runs SET text=:content, revision=revision+1 WHERE id=:id'),
                {'content': 'secret produced after revocation', 'id': UUID(run['id'])},
            )
        remaining = [line async for line in lines]
        assert not any('secret produced after revocation' in line for line in remaining)


async def test_sse_disconnect_releases_the_http_task_without_cancelling_run(
    admin: httpx.AsyncClient,
    model_id: str,
    app: object,
    tcp_server: tuple[uvicorn.Server, str],
) -> None:
    _conversation, run = await admit(admin, model_id)
    server, base_url = tcp_server
    port = int(base_url.rsplit(':', 1)[1])
    reader, writer = await asyncio.open_connection('127.0.0.1', port)
    writer.write(
        (
            f'GET /api/runs/{run["id"]}/events HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n'
            f'Cookie: zhiheng_session={admin.cookies.get("zhiheng_session")}\r\n\r\n'
        ).encode()
    )
    await writer.drain()
    await reader.readuntil(b'event: snapshot')
    writer.close()
    await writer.wait_closed()
    deadline = monotonic() + 1
    # Uvicorn exposes its active response tasks without an awaitable state event.
    while server.server_state.tasks and monotonic() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(0.02)
    assert not server.server_state.tasks
    async with app.state.container.database.sessions() as session:
        snapshot = await app.state.container.runs.snapshot(
            session, UUID(run['id']), UUID((await admin.get('/api/auth/me')).json()['id'])
        )
    assert snapshot.status == 'pending'
