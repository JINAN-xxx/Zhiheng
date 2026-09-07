import asyncio
from pathlib import Path

from config.database import AsyncSessionLocal
from plugins.agent_extension.config import AgentSkillSettings
from plugins.agent_extension.domain.skill_package import SkillPackageLimits, SkillPackageParser
from plugins.agent_extension.infrastructure.skill_storage import SkillStorage
from plugins.agent_extension.service.skill_service import SkillContextProvider, SkillService
from plugins.core.runtime.hooks import PluginHookContext

BUILTIN_ROOT = Path(__file__).parent / 'buildin'


def _runtime() -> tuple[AgentSkillSettings, SkillStorage]:
    settings = AgentSkillSettings()
    parser = SkillPackageParser(
        SkillPackageLimits(
            upload_bytes=settings.upload_bytes,
            extracted_bytes=settings.extracted_bytes,
            file_count=settings.file_count,
            skill_md_bytes=settings.skill_md_bytes,
        )
    )
    return settings, SkillStorage(
        settings.data_dir,
        parser=parser,
        draft_ttl_seconds=settings.draft_ttl_seconds,
    )


async def _sync_builtin_skills(storage: SkillStorage, settings: AgentSkillSettings) -> None:
    async with AsyncSessionLocal() as query_db:
        await SkillService(query_db, storage, settings).sync_builtin_skills(BUILTIN_ROOT)


async def on_startup(context: PluginHookContext) -> None:
    """初始化技能存储并向对话运行时注册只读上下文提供器。"""
    if context.app is None:
        raise RuntimeError('智能体扩展插件启动缺少 FastAPI 应用上下文')
    settings, storage = await asyncio.to_thread(_runtime)
    await asyncio.to_thread(storage.cleanup_stale, limit=settings.cleanup_limit)
    if context.startup_write_enabled:
        await _sync_builtin_skills(storage, settings)
    context.app.state.agent_skill_context_provider = SkillContextProvider(storage, settings)


async def on_shutdown(context: PluginHookContext) -> None:
    """插件停用时清理已注册的技能运行时。"""
    if context.app is not None and hasattr(context.app.state, 'agent_skill_context_provider'):
        delattr(context.app.state, 'agent_skill_context_provider')
