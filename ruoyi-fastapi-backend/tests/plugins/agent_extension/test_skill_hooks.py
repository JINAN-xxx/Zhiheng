from types import SimpleNamespace

import pytest

from plugins.agent_extension import hooks

CLEANUP_LIMIT = 100


class FakeStorage:
    def cleanup_stale(self, *, limit: int) -> int:
        assert limit == CLEANUP_LIMIT
        return 0


@pytest.mark.asyncio
async def test_startup_writer_syncs_builtins_without_context_database(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(cleanup_limit=CLEANUP_LIMIT)
    storage = FakeStorage()
    calls: list[tuple[object, object]] = []

    async def sync(current_storage: object, current_settings: object) -> None:
        calls.append((current_storage, current_settings))

    monkeypatch.setattr(hooks, '_runtime', lambda: (settings, storage))
    monkeypatch.setattr(hooks, '_sync_builtin_skills', sync)
    context = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), startup_write_enabled=True)

    await hooks.on_startup(context)

    assert calls == [(storage, settings)]
    assert context.app.state.agent_skill_context_provider is not None


@pytest.mark.asyncio
async def test_startup_reader_registers_provider_without_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(cleanup_limit=CLEANUP_LIMIT)
    storage = FakeStorage()

    async def fail_sync(_storage: object, _settings: object) -> None:
        raise AssertionError('reader must not sync builtins')

    monkeypatch.setattr(hooks, '_runtime', lambda: (settings, storage))
    monkeypatch.setattr(hooks, '_sync_builtin_skills', fail_sync)
    context = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()), startup_write_enabled=False)

    await hooks.on_startup(context)

    assert context.app.state.agent_skill_context_provider is not None
