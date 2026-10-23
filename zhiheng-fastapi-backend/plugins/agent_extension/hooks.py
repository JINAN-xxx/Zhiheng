import asyncio
from pathlib import Path

from config.database import AsyncSessionLocal
from plugins.agent_extension.config import AgentSkillSettings
from plugins.agent_extension.domain.skill_package import SkillPackageLimits, SkillPackageParser
from plugins.agent_extension.infrastructure.knowledge_context_provider import PlatformKnowledgeProvider
from plugins.agent_extension.infrastructure.skill_storage import SkillStorage
from plugins.agent_extension.service.skill_service import SkillContextProvider, SkillService
from plugins.agent_extension.service.work_task_run_service import WorkTaskRunService
from plugins.core.runtime.hooks import PluginHookContext
from utils.log_util import logger

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


async def _recover_work_runs() -> None:
    await WorkTaskRunService(AsyncSessionLocal, model_runtime=None).recover_expired()


async def _work_recovery_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await _recover_work_runs()
        except Exception:
            # A temporary database outage must not permanently disable lease recovery.
            logger.warning('工作任务过期运行恢复暂时失败，将在下次巡检重试')


async def on_startup(context: PluginHookContext) -> None:
    """初始化技能存储并向对话运行时注册只读上下文提供器。"""
    if context.app is None:
        raise RuntimeError('智能体扩展插件启动缺少 FastAPI 应用上下文')
    settings, storage = await asyncio.to_thread(_runtime)
    await asyncio.to_thread(storage.cleanup_stale, limit=settings.cleanup_limit)
    if context.startup_write_enabled:
        await _sync_builtin_skills(storage, settings)
    # Recovery belongs to each process lifecycle, including ready-state readers.
    # Conditional database updates make overlapping scans safe across workers.
    recovery_task = getattr(context.app.state, 'agent_work_recovery_task', None)
    if recovery_task is None or recovery_task.done():
        await _recover_work_runs()
        context.app.state.agent_work_recovery_task = asyncio.create_task(_work_recovery_loop())
    context.app.state.agent_work_session_factory = AsyncSessionLocal
    context.app.state.agent_skill_context_provider = SkillContextProvider(storage, settings)
    context.app.state.agent_knowledge_provider = PlatformKnowledgeProvider(context.app)


async def on_shutdown(context: PluginHookContext) -> None:
    """插件停用时清理已注册的技能运行时。"""
    if context.app is not None and hasattr(context.app.state, 'agent_work_recovery_task'):
        task = context.app.state.agent_work_recovery_task
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        delattr(context.app.state, 'agent_work_recovery_task')
    if context.app is not None and hasattr(context.app.state, 'agent_skill_context_provider'):
        delattr(context.app.state, 'agent_skill_context_provider')
    if context.app is not None and hasattr(context.app.state, 'agent_knowledge_provider'):
        delattr(context.app.state, 'agent_knowledge_provider')
    if context.app is not None and hasattr(context.app.state, 'agent_work_session_factory'):
        delattr(context.app.state, 'agent_work_session_factory')
