from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from plugins.ai import hooks
from plugins.ai.config import LiteLLMSettings


class FakeRuntimeClient:
    """测试用 Runtime 客户端。"""

    def __init__(self, settings: LiteLLMSettings) -> None:
        self.settings = settings
        self.health_call_count = 0
        self.close_call_count = 0

    async def check_liveliness(self) -> dict[str, str]:
        self.health_call_count += 1
        return {'status': 'healthy'}

    async def aclose(self) -> None:
        self.close_call_count += 1


@pytest.mark.asyncio
async def test_startup_creates_worker_client_without_health_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """启动 Hook 只创建连接池，不用外部依赖阻塞应用启动。"""
    app = FastAPI()
    context = SimpleNamespace(app=app)
    monkeypatch.setattr(hooks, 'LiteLLMRuntimeClient', FakeRuntimeClient)

    await hooks.on_startup(context)

    client = app.state.ai_litellm_runtime_client
    assert isinstance(client, FakeRuntimeClient)
    assert client.health_call_count == 0


@pytest.mark.asyncio
async def test_startup_allows_missing_runtime_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime Key 为空时 AI 插件仍能完成启动。"""
    app = FastAPI()
    context = SimpleNamespace(app=app)
    monkeypatch.delenv('LITELLM_RUNTIME_KEY', raising=False)
    monkeypatch.setattr(hooks, 'LiteLLMRuntimeClient', FakeRuntimeClient)

    await hooks.on_startup(context)

    client = app.state.ai_litellm_runtime_client
    assert client.settings.configured is False


@pytest.mark.asyncio
async def test_shutdown_is_idempotent_and_removes_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭 Hook 只释放一次连接池，重复调用无副作用。"""
    app = FastAPI()
    context = SimpleNamespace(app=app)
    monkeypatch.setattr(hooks, 'LiteLLMRuntimeClient', FakeRuntimeClient)
    await hooks.on_startup(context)
    client = app.state.ai_litellm_runtime_client

    await hooks.on_shutdown(context)
    await hooks.on_shutdown(context)

    assert client.close_call_count == 1
    assert not hasattr(app.state, 'ai_litellm_runtime_client')


@pytest.mark.asyncio
async def test_shutdown_without_application_context_is_safe() -> None:
    """缺少应用上下文时关闭 Hook 保持幂等。"""
    await hooks.on_shutdown(SimpleNamespace(app=None))
