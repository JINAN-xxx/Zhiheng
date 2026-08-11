from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_client import LiteLLMRuntimeClient
from plugins.core.runtime.hooks import PluginHookContext

CLIENT_STATE_NAME = 'ai_litellm_runtime_client'


async def on_startup(context: PluginHookContext) -> None:
    """为当前 FastAPI Worker 创建 LiteLLM 连接池。"""
    if context.app is None:
        raise RuntimeError('AI 插件启动缺少 FastAPI 应用上下文')
    settings = LiteLLMSettings()
    setattr(context.app.state, CLIENT_STATE_NAME, LiteLLMRuntimeClient(settings))


async def on_shutdown(context: PluginHookContext) -> None:
    """释放当前 FastAPI Worker 的 LiteLLM 连接池。"""
    if context.app is None:
        return
    client = getattr(context.app.state, CLIENT_STATE_NAME, None)
    if client is None:
        return
    await client.aclose()
    delattr(context.app.state, CLIENT_STATE_NAME)
