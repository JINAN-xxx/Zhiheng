import asyncio
from types import SimpleNamespace
from typing import get_type_hints

import pytest
from fastapi import FastAPI

from plugins.ai import hooks
from plugins.ai.config import LiteLLMSettings

EXPECTED_RETRY_CLOSE_CALLS = 2


def test_runtime_state_name_keeps_backward_compatible_export() -> None:
    assert hooks.CLIENT_STATE_NAME == hooks.RUNTIME_CLIENT_STATE_NAME


def test_close_helper_uses_structural_async_closable_contract() -> None:
    assert get_type_hints(hooks._capture_close_error)['client'] is hooks.AsyncClosable


class FakeRuntimeClient:
    """测试用 Runtime 客户端。"""

    def __init__(self, settings: LiteLLMSettings, *, close_error: BaseException | None = None) -> None:
        self.settings = settings
        self.health_call_count = 0
        self.close_call_count = 0
        self.close_error = close_error

    async def check_liveliness(self) -> dict[str, str]:
        self.health_call_count += 1
        return {'status': 'healthy'}

    async def aclose(self) -> None:
        self.close_call_count += 1
        if self.close_error is not None:
            raise self.close_error


class FakeManagementClient(FakeRuntimeClient):
    """测试用 Management 客户端。"""


def patch_client_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hooks, 'LiteLLMRuntimeClient', FakeRuntimeClient)
    monkeypatch.setattr(hooks, 'LiteLLMManagementClient', FakeManagementClient)


@pytest.mark.asyncio
async def test_startup_creates_worker_client_without_health_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """启动 Hook 只创建连接池，不用外部依赖阻塞应用启动。"""
    app = FastAPI()
    context = SimpleNamespace(app=app)
    patch_client_types(monkeypatch)

    await hooks.on_startup(context)

    client = app.state.ai_litellm_runtime_client
    management_client = app.state.ai_litellm_management_client
    assert isinstance(client, FakeRuntimeClient)
    assert isinstance(management_client, FakeManagementClient)
    assert client.health_call_count == 0
    assert management_client.health_call_count == 0
    assert client.settings is management_client.settings


@pytest.mark.asyncio
async def test_startup_allows_missing_runtime_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runtime Key 为空时 AI 插件仍能完成启动。"""
    app = FastAPI()
    context = SimpleNamespace(app=app)
    monkeypatch.delenv('LITELLM_RUNTIME_KEY', raising=False)
    monkeypatch.delenv('LITELLM_MANAGEMENT_KEY', raising=False)
    patch_client_types(monkeypatch)

    await hooks.on_startup(context)

    client = app.state.ai_litellm_runtime_client
    management_client = app.state.ai_litellm_management_client
    assert client.settings.configured is False
    assert management_client.settings.management_configured is False


@pytest.mark.asyncio
async def test_startup_closes_runtime_client_when_management_construction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    context = SimpleNamespace(app=app)
    runtime_clients: list[FakeRuntimeClient] = []

    class RecordingRuntimeClient(FakeRuntimeClient):
        def __init__(self, settings: LiteLLMSettings) -> None:
            super().__init__(settings)
            runtime_clients.append(self)

    class FailingManagementClient:
        def __init__(self, settings: LiteLLMSettings) -> None:
            raise RuntimeError('management construction failed')

    monkeypatch.setattr(hooks, 'LiteLLMRuntimeClient', RecordingRuntimeClient)
    monkeypatch.setattr(hooks, 'LiteLLMManagementClient', FailingManagementClient)

    with pytest.raises(RuntimeError, match='management construction failed'):
        await hooks.on_startup(context)

    assert len(runtime_clients) == 1
    assert runtime_clients[0].close_call_count == 1
    assert not hasattr(app.state, 'ai_litellm_runtime_client')
    assert not hasattr(app.state, 'ai_litellm_management_client')


@pytest.mark.asyncio
async def test_startup_retains_runtime_ownership_when_compensating_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    context = SimpleNamespace(app=app)
    runtime_client = FakeRuntimeClient(LiteLLMSettings(), close_error=RuntimeError('runtime close failed'))

    monkeypatch.setattr(hooks, 'LiteLLMRuntimeClient', lambda settings: runtime_client)

    class FailingManagementClient:
        def __init__(self, settings: LiteLLMSettings) -> None:
            raise RuntimeError('management construction failed')

    monkeypatch.setattr(hooks, 'LiteLLMManagementClient', FailingManagementClient)

    with pytest.raises(RuntimeError, match='management construction failed'):
        await hooks.on_startup(context)

    assert app.state.ai_litellm_runtime_client is runtime_client
    runtime_client.close_error = None

    await hooks.on_shutdown(context)

    assert runtime_client.close_call_count == EXPECTED_RETRY_CLOSE_CALLS
    assert not hasattr(app.state, 'ai_litellm_runtime_client')


@pytest.mark.asyncio
async def test_startup_prioritizes_cancellation_from_compensating_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    context = SimpleNamespace(app=app)
    cancellation = asyncio.CancelledError()
    runtime_client = FakeRuntimeClient(LiteLLMSettings(), close_error=cancellation)

    monkeypatch.setattr(hooks, 'LiteLLMRuntimeClient', lambda settings: runtime_client)

    class FailingManagementClient:
        def __init__(self, settings: LiteLLMSettings) -> None:
            raise RuntimeError('management construction failed')

    monkeypatch.setattr(hooks, 'LiteLLMManagementClient', FailingManagementClient)

    with pytest.raises(asyncio.CancelledError) as raised:
        await hooks.on_startup(context)

    assert raised.value is cancellation
    assert app.state.ai_litellm_runtime_client is runtime_client


@pytest.mark.asyncio
async def test_shutdown_is_idempotent_and_removes_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭 Hook 只释放一次连接池，重复调用无副作用。"""
    app = FastAPI()
    context = SimpleNamespace(app=app)
    patch_client_types(monkeypatch)
    await hooks.on_startup(context)
    runtime_client = app.state.ai_litellm_runtime_client
    management_client = app.state.ai_litellm_management_client

    await hooks.on_shutdown(context)
    await hooks.on_shutdown(context)

    assert runtime_client.close_call_count == 1
    assert management_client.close_call_count == 1
    assert not hasattr(app.state, 'ai_litellm_runtime_client')
    assert not hasattr(app.state, 'ai_litellm_management_client')


@pytest.mark.asyncio
async def test_shutdown_closes_partial_management_state() -> None:
    app = FastAPI()
    context = SimpleNamespace(app=app)
    management_client = FakeManagementClient(LiteLLMSettings())
    app.state.ai_litellm_management_client = management_client

    await hooks.on_shutdown(context)

    assert management_client.close_call_count == 1
    assert not hasattr(app.state, 'ai_litellm_management_client')


@pytest.mark.asyncio
async def test_shutdown_closes_other_client_and_removes_state_after_regular_failure() -> None:
    app = FastAPI()
    context = SimpleNamespace(app=app)
    runtime_client = FakeRuntimeClient(LiteLLMSettings(), close_error=RuntimeError('runtime close failed'))
    management_client = FakeManagementClient(LiteLLMSettings())
    app.state.ai_litellm_runtime_client = runtime_client
    app.state.ai_litellm_management_client = management_client

    with pytest.raises(RuntimeError, match='runtime close failed'):
        await hooks.on_shutdown(context)

    assert runtime_client.close_call_count == 1
    assert management_client.close_call_count == 1
    assert app.state.ai_litellm_runtime_client is runtime_client
    assert not hasattr(app.state, 'ai_litellm_management_client')

    runtime_client.close_error = None
    await hooks.on_shutdown(context)

    assert runtime_client.close_call_count == EXPECTED_RETRY_CLOSE_CALLS
    assert not hasattr(app.state, 'ai_litellm_runtime_client')


@pytest.mark.asyncio
async def test_shutdown_preserves_cancellation_after_closing_other_client() -> None:
    app = FastAPI()
    context = SimpleNamespace(app=app)
    cancellation = asyncio.CancelledError()
    runtime_client = FakeRuntimeClient(LiteLLMSettings(), close_error=cancellation)
    management_client = FakeManagementClient(LiteLLMSettings(), close_error=RuntimeError('secondary close failed'))
    app.state.ai_litellm_runtime_client = runtime_client
    app.state.ai_litellm_management_client = management_client

    with pytest.raises(asyncio.CancelledError) as raised:
        await hooks.on_shutdown(context)

    assert raised.value is cancellation
    assert runtime_client.close_call_count == 1
    assert management_client.close_call_count == 1
    assert app.state.ai_litellm_runtime_client is runtime_client
    assert app.state.ai_litellm_management_client is management_client

    runtime_client.close_error = None
    management_client.close_error = None
    await hooks.on_shutdown(context)

    assert runtime_client.close_call_count == EXPECTED_RETRY_CLOSE_CALLS
    assert management_client.close_call_count == EXPECTED_RETRY_CLOSE_CALLS
    assert not hasattr(app.state, 'ai_litellm_runtime_client')
    assert not hasattr(app.state, 'ai_litellm_management_client')


@pytest.mark.asyncio
async def test_shutdown_without_application_context_is_safe() -> None:
    """缺少应用上下文时关闭 Hook 保持幂等。"""
    await hooks.on_shutdown(SimpleNamespace(app=None))
