import asyncio
import logging
from collections.abc import Awaitable, Callable
from uuid import UUID

from zhiheng.application.execution.failures import database_sqlstate, exception_frames
from zhiheng.bootstrap.container import Container
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

logger = logging.getLogger(__name__)
HOUSEKEEPING_TIMEOUT_SECONDS = 10


async def recover_work(container: Container) -> None:
    await container.run_recovery.recover_expired()
    await container.chat_context.reconcile()
    await container.assistant_agent.reconcile()
    await container.work_agent.reconcile()


async def run_cleanup_step(name: str, operation: Callable[[], Awaitable[object]], *, timeout_seconds: float) -> None:
    try:
        await asyncio.wait_for(operation(), timeout=timeout_seconds)
    except Exception as error:
        logger.error(
            '%s failed error_type=%s sqlstate=%s frames=%s',
            name,
            type(error).__name__,
            database_sqlstate(error),
            exception_frames(error),
        )


async def housekeeping(container: Container) -> None:
    async def expire_skill_drafts() -> None:
        async with container.database.sessions.begin() as session:
            await container.skills.cleanup_expired(session)

    for name, operation in (
        ('Skill draft cleanup', expire_skill_drafts),
        ('Chat checkpoint cleanup', container.chat_agent.cleanup_checkpoints),
        ('Assistant checkpoint cleanup', container.assistant_agent.cleanup_checkpoints),
    ):
        await run_cleanup_step(name, operation, timeout_seconds=HOUSEKEEPING_TIMEOUT_SECONDS)


async def file_maintenance(container: Container) -> None:
    for name, operation in (
        ('Knowledge upload cleanup', container.knowledge_documents.cleanup_uploads),
        ('Retired knowledge chunk cleanup', container.knowledge_jobs.cleanup_retired_chunks),
    ):
        await run_cleanup_step(name, operation, timeout_seconds=HOUSEKEEPING_TIMEOUT_SECONDS)
    system = Principal(UUID(int=0), 'files-maintenance', True)
    try:
        for kind in ('reminders', 'expiry', 'purge', 'reconcile'):
            await container.file_jobs.run(system, kind)
    except AppError as error:
        if error.code != 'file_job_busy':
            raise
