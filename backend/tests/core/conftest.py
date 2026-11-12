import json
import os
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tests.core.provider import ProviderServer
from tests.core.resources import resource

if TYPE_CHECKING:
    from fastapi import FastAPI

    from zhiheng.bootstrap.settings import Settings


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


@pytest_asyncio.fixture(scope='session', loop_scope='session')
async def schema_template(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[str | None]:
    """Opt-in schema reuse; every app still receives its own independently mutable database."""
    database_url = os.getenv('ZHIHENG_TEST_DATABASE_URL')
    redis_url = os.getenv('ZHIHENG_TEST_REDIS_URL')
    if os.getenv('ZHIHENG_TEST_SCHEMA_TEMPLATE') != '1' or not database_url or not redis_url:
        yield None
        return
    from zhiheng.bootstrap.migrations import migrate
    from zhiheng.bootstrap.settings import Settings

    name = f'core_test_schema_{uuid4().hex}'
    resource('intent', 'postgres_database', name)
    admin = create_async_engine(database_url, isolation_level='AUTOCOMMIT')
    created = False
    try:
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        created = True
        resource('created', 'postgres_database', name)
        url = make_url(database_url).set(database=name).render_as_string(hide_password=False)
        temporary = tmp_path_factory.mktemp('core-schema-template')
        await migrate(
            Settings(
                files_root=temporary / 'files',
                graph_working_root=temporary / 'graph-sdk',
                database_url=url,
                redis_url=redis_url,
                credential_key=Fernet.generate_key().decode(),
                worker_queue=f'core:schema:{uuid4().hex}',
                cookie_secure=False,
            )
        )
        # A template contains migrations and default catalog data, never test accounts or requests.
        async with admin.connect() as connection:
            await connection.execute(text(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false'))
        yield name
    finally:
        try:
            if created:
                async with admin.connect() as connection:
                    await connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
                resource('deleted', 'postgres_database', name)
        finally:
            await admin.dispose()


@pytest_asyncio.fixture
async def settings(  # noqa: PLR0912, PLR0915 - keep ownership registration and exact resource cleanup in the same fixture lifecycle
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    schema_template: str | None,
) -> AsyncIterator['Settings']:
    from zhiheng.bootstrap.settings import Settings

    database_url = os.getenv('ZHIHENG_TEST_DATABASE_URL')
    redis_url = os.getenv('ZHIHENG_TEST_REDIS_URL')
    if not database_url or not redis_url:
        pytest.skip('Separate PostgreSQL and Redis URLs are required for integration tests')
    database_name = f'core_test_{uuid4().hex}'
    resource('intent', 'postgres_database', database_name)
    admin = create_async_engine(database_url, isolation_level='AUTOCOMMIT')
    async with admin.connect() as connection:
        # Migration-specific settings-only cases retain an empty database.
        template = f' TEMPLATE "{schema_template}"' if schema_template and 'app' in request.fixturenames else ''
        await connection.execute(text(f'CREATE DATABASE "{database_name}"{template}'))
    resource('created', 'postgres_database', database_name)
    url = make_url(database_url).set(database=database_name).render_as_string(hide_password=False)
    from arq.connections import ArqRedis
    from arq.jobs import Job
    from redis.asyncio import Redis
    from sqlalchemy import event

    from zhiheng.runtime.orm import Outbox

    instance = Settings(
        files_root=tmp_path / 'files',
        graph_working_root=tmp_path / 'graph-sdk',
        database_url=url,
        redis_url=redis_url,
        credential_key=Fernet.generate_key().decode(),
        cookie_secure=False,
        worker_queue=f'core:test:{uuid4().hex}',
        provider_timeout_seconds=2,
        lease_seconds=3,
    )
    exact_keys = {*instance.worker_queues, instance.worker_health_key, *instance.worker_arq_health_keys}
    for key in exact_keys:
        resource('intent', 'redis_key', key)

    def register_job(job_id: str) -> None:
        for prefix in ('arq:job:', 'arq:result:', 'arq:in-progress:', 'arq:retry:'):
            key = prefix + job_id
            if key not in exact_keys:
                resource('intent', 'redis_key', key)
                exact_keys.add(key)

    def register_outbox(mapper: object, connection: object, target: Outbox) -> None:
        if target.id is None:
            target.id = uuid4()
        register_job(str(target.id))

    event.listen(Outbox, 'before_insert', register_outbox)
    original = ArqRedis.enqueue_job

    async def register_enqueue(queue: ArqRedis, *args: object, **kwargs: object) -> Job | None:
        job_id = kwargs.get('_job_id')
        if isinstance(job_id, str):
            register_job(job_id)
        return await original(queue, *args, **kwargs)

    monkeypatch.setattr(ArqRedis, 'enqueue_job', register_enqueue)
    from zhiheng.modules.knowledge.adapters.lightrag.transport import GraphVectorRPC

    original_rpc = GraphVectorRPC._invoke

    def register_graph_rpc(
        owner: GraphVectorRPC, name: str, args: tuple[object, ...], kwargs: dict[str, object]
    ) -> Any:
        if name == 'create_collection':
            collection = args[0] if args else kwargs.get('collection_name')
            schema = kwargs.get('schema')
            resource(
                'intent',
                'milvus_collection',
                str(collection),
                description=getattr(schema, 'description', ''),
                database='default',
            )
        return original_rpc(owner, name, args, kwargs)

    monkeypatch.setattr(GraphVectorRPC, '_invoke', register_graph_rpc)

    try:
        yield instance
    finally:
        event.remove(Outbox, 'before_insert', register_outbox)
        journal = os.getenv('ZHIHENG_TEST_RESOURCE_LOG')
        if journal and Path(journal).exists():  # noqa: ASYNC240 - inspect the local ownership journal without adding a teardown scheduling point
            events = [json.loads(line) for line in Path(journal).read_text().splitlines() if line]  # noqa: ASYNC240 - preserve the synchronous journal snapshot before child/key cleanup
            children = {
                int(row['name'])
                for row in events
                if row['kind'] == 'worker_process'
                and row['action'] == 'created'
                and row.get('queue') == instance.worker_queue
                and row['name'].isdigit()
            }
            for child in children:
                command = Path(f'/proc/{child}/cmdline')
                if command.exists() and b'tests.core.graph_worker_process' in command.read_bytes():  # noqa: ASYNC240 - keep the local procfs ownership check contiguous before deleting resources
                    raise AssertionError('Owned child worker must stop before fixture cleanup')
            for row in events:
                if row['kind'] == 'redis_key' and row.get('process_id') in children:
                    exact_keys.add(row['name'])
        facts = create_async_engine(url)
        try:
            async with facts.connect() as connection:
                if await connection.scalar(text("SELECT to_regclass('public.runtime_outbox') IS NOT NULL")):
                    for identifier in (await connection.execute(text('SELECT id FROM runtime_outbox'))).scalars():
                        register_job(str(identifier))
        finally:
            await facts.dispose()
        redis = Redis.from_url(redis_url, decode_responses=True)
        try:
            for key in exact_keys:
                await redis.delete(key)
                if await redis.exists(key):
                    raise AssertionError('Owned Redis key remains')
                resource('deleted', 'redis_key', key)
        finally:
            await redis.aclose()
        async with admin.connect() as connection:
            await connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        resource('deleted', 'postgres_database', database_name)
        await admin.dispose()


@pytest_asyncio.fixture
async def app(settings: 'Settings') -> AsyncIterator['FastAPI']:
    from zhiheng.api.main import create_app
    from zhiheng.bootstrap.migrations import migrate

    await migrate(settings)
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        container = application.state.container
        async with container.database.sessions.begin() as session:
            await container.accounts.create_user(
                session, username='admin', password='platform-test-password', is_admin=True
            )
            await container.accounts.create_user(
                session, username='member', password='platform-test-password', is_admin=False
            )
        yield application


@pytest_asyncio.fixture
async def client(app: 'FastAPI') -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as value:
        yield value


@pytest_asyncio.fixture
async def admin(client: httpx.AsyncClient) -> httpx.AsyncClient:
    result = await client.post('/api/auth/login', json={'username': 'admin', 'password': 'platform-test-password'})
    assert result.status_code == 200, result.text
    return client
