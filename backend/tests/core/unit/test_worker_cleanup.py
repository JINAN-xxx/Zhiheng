import pytest
from cryptography.fernet import Fernet

from zhiheng.bootstrap.settings import Settings


@pytest.mark.parametrize('failure', ['construction', 'worker_close', 'heartbeat_delete'])
async def test_worker_always_releases_its_container(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    from zhiheng.worker import main

    settings = Settings(
        database_url='postgresql+asyncpg://user:password@localhost/core',
        redis_url='redis://localhost:6379/0',
        credential_key=Fernet.generate_key().decode(),
    )

    class Redis:
        async def delete(self, _key: str) -> None:
            if failure == 'heartbeat_delete':
                raise RuntimeError('heartbeat deletion failed')

    class Resources:
        closed = False
        redis = Redis()

        async def close(self) -> None:
            self.closed = True

    class Worker:
        async def async_run(self) -> None:
            pass

        async def close(self) -> None:
            if failure == 'worker_close':
                raise RuntimeError('worker close failed')

    resources = Resources()

    def build(_container: object) -> Worker:
        if failure == 'construction':
            raise RuntimeError('worker construction failed')
        return Worker()

    monkeypatch.setattr(main, 'Container', lambda _settings: resources)
    monkeypatch.setattr(main, 'build_worker', build)
    with pytest.raises(RuntimeError):
        await main.run_worker(settings)
    assert resources.closed
