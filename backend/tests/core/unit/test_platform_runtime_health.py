from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet

from zhiheng.api.main import create_app
from zhiheng.bootstrap.container import Container
from zhiheng.bootstrap.settings import Settings


@pytest.mark.parametrize('failures', [(), ('graph',), ('redis',), ('database',), ('graph', 'redis', 'database')])
async def test_all_resources_close_when_any_closer_fails(failures: tuple[str, ...]) -> None:
    calls = []
    errors = {name: RuntimeError(name) for name in failures}

    def resource(name: str) -> SimpleNamespace:
        async def close() -> None:
            calls.append(name)
            if name in errors:
                raise errors[name]

        return SimpleNamespace(close=close, aclose=close)

    container = object.__new__(Container)
    container.graph_sdk, container.redis, container.database = (
        resource(name) for name in ('graph', 'redis', 'database')
    )
    if errors:
        with pytest.raises(RuntimeError) as caught:
            await container.close()
        chain = []
        error = caught.value
        while error is not None:
            chain.append(str(error))
            error = error.__context__
        assert set(failures) <= set(chain)
    else:
        await container.close()
    assert calls == ['graph', 'redis', 'database']


@pytest.mark.parametrize(
    ('heartbeat', 'status'),
    [
        (None, 503),
        ('nan', 503),
        ('inf', 503),
        ('-inf', 503),
        ('invalid', 503),
        ('1001', 503),
        ('984.9', 503),
        ('985', 200),
        ('1000', 200),
        ('995', 200),
    ],
)
async def test_readiness_requires_finite_nonfuture_recent_heartbeat(
    monkeypatch: pytest.MonkeyPatch, heartbeat: str | None, status: int
) -> None:
    monkeypatch.setattr('zhiheng.api.main.time', lambda: 1000.0)

    async def ok() -> None:
        pass

    async def get(_key: str) -> str | None:
        return heartbeat

    settings = Settings(
        database_url='postgresql+asyncpg://test:test@localhost/test',
        redis_url='redis://localhost/0',
        credential_key=Fernet.generate_key().decode(),
    )
    app = create_app(settings)
    app.state.container = SimpleNamespace(
        database=SimpleNamespace(require_schema=ok),
        redis=SimpleNamespace(ping=ok, get=get),
        files=SimpleNamespace(storage=SimpleNamespace(check=ok)),
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        assert (await client.get('/health/ready')).status_code == status
