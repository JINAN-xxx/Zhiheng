from plugins.core.runtime.hooks import PluginHookContext


async def on_startup(context: PluginHookContext) -> None:
    """技能运行时在管理服务任务中注册。"""
    del context


async def on_shutdown(context: PluginHookContext) -> None:
    """插件停用时清理已注册的技能运行时。"""
    if context.app is not None and hasattr(context.app.state, 'agent_skill_context_provider'):
        delattr(context.app.state, 'agent_skill_context_provider')

