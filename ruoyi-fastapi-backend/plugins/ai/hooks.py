import asyncio

from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient
from plugins.ai.infrastructure.litellm_management_client import LiteLLMManagementClient
from plugins.core.runtime.hooks import PluginHookContext

RUNTIME_CLIENT_STATE_NAME = 'ai_litellm_runtime_client'
MANAGEMENT_CLIENT_STATE_NAME = 'ai_litellm_management_client'
CLIENT_STATE_NAME = RUNTIME_CLIENT_STATE_NAME


async def on_startup(context: PluginHookContext) -> None:
    """为当前 FastAPI Worker 创建 LiteLLM 连接池。"""
    if context.app is None:
        raise RuntimeError('AI 插件启动缺少 FastAPI 应用上下文')
    settings = LiteLLMSettings()
    runtime_client = LiteLLMRuntimeClient(settings)
    try:
        management_client = LiteLLMManagementClient(settings)
    except BaseException:
        close_error = await _capture_close_error(runtime_client)
        if close_error is not None:
            setattr(context.app.state, RUNTIME_CLIENT_STATE_NAME, runtime_client)
        if isinstance(close_error, asyncio.CancelledError):
            raise close_error from None
        raise
    setattr(context.app.state, RUNTIME_CLIENT_STATE_NAME, runtime_client)
    setattr(context.app.state, MANAGEMENT_CLIENT_STATE_NAME, management_client)


async def on_shutdown(context: PluginHookContext) -> None:
    """释放当前 FastAPI Worker 的 LiteLLM 连接池。"""
    if context.app is None:
        return
    runtime_error = await _close_state_client(context.app.state, RUNTIME_CLIENT_STATE_NAME)
    management_error = await _close_state_client(context.app.state, MANAGEMENT_CLIENT_STATE_NAME)

    cancellation = _first_error_of_type((runtime_error, management_error), asyncio.CancelledError)
    if cancellation is not None:
        raise cancellation
    close_error = runtime_error or management_error
    if close_error is not None:
        raise close_error


async def _close_state_client(app_state: object, state_name: str) -> BaseException | None:
    client = getattr(app_state, state_name, None)
    if client is None:
        return None
    close_error = await _capture_close_error(client)
    if close_error is not None:
        return close_error
    delattr(app_state, state_name)
    return None


async def _capture_close_error(client: object) -> BaseException | None:
    try:
        await client.aclose()
    except asyncio.CancelledError as error:
        return error
    except Exception as error:
        return error
    return None


def _first_error_of_type(
    errors: tuple[BaseException | None, ...],
    error_type: type[BaseException],
) -> BaseException | None:
    return next((error for error in errors if isinstance(error, error_type)), None)
