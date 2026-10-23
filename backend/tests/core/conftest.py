import os
import threading
from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.core.provider import ProviderServer


@pytest.fixture
def provider() -> Iterator[ProviderServer]:
    server = ProviderServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest_asyncio.fixture
async def model_id(admin: httpx.AsyncClient, provider: ProviderServer) -> str:
    connection = await admin.post(
        '/api/models/connections',
        json={
            'name': 'Local test provider',
            'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
            'api_key': 'local-test-key',
        },
    )
    assert connection.status_code == 201, connection.text
    result = await admin.post(
        '/api/models',
        json={
            'name': 'Local text',
            'connection_id': connection.json()['id'],
            'upstream_model': 'local-text',
            'max_output_tokens': 64,
        },
    )
    assert result.status_code == 201, result.text
    return result.json()['id']


@pytest_asyncio.fixture
async def settings() -> AsyncIterator[object]:
    from zhiheng.bootstrap.settings import Settings

    database_url = os.getenv('ZHIHENG_TEST_DATABASE_URL')
    redis_url = os.getenv('ZHIHENG_TEST_REDIS_URL')
    if not database_url or not redis_url:
        pytest.skip('Separate PostgreSQL and Redis URLs are required for integration tests')
    database_name = f'core_test_{uuid4().hex}'
    admin = create_async_engine(database_url, isolation_level='AUTOCOMMIT')
    async with admin.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    url = make_url(database_url).set(database=database_name).render_as_string(hide_password=False)
    try:
        yield Settings(
            database_url=url,
            redis_url=redis_url,
            credential_key=Fernet.generate_key().decode(),
            cookie_secure=False,
            worker_queue=f'core:test:{uuid4().hex}',
            provider_timeout_seconds=2,
            lease_seconds=3,
        )
    finally:
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        await admin.dispose()


@pytest_asyncio.fixture
async def app(settings: object) -> AsyncIterator[object]:
    from zhiheng.api.main import create_app
    from zhiheng.bootstrap.migrations import migrate
    from zhiheng.modules.identity.public import IdentityService

    await migrate(settings)
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        container = application.state.container
        async with container.database.sessions.begin() as session:
            await IdentityService().create_user(
                session, username='admin', password='platform-test-password', is_admin=True
            )
            await IdentityService().create_user(
                session, username='member', password='platform-test-password', is_admin=False
            )
        yield application


@pytest_asyncio.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as value:
        yield value


@pytest_asyncio.fixture
async def admin(client: httpx.AsyncClient) -> httpx.AsyncClient:
    result = await client.post('/api/auth/login', json={'username': 'admin', 'password': 'platform-test-password'})
    assert result.status_code == 200, result.text
    return client
