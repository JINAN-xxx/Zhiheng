from uuid import UUID

from zhiheng.bootstrap.container import Container
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


async def maintain(container: Container) -> None:
    async with container.database.sessions.begin() as session:
        await container.skills.cleanup_expired(session)
    await container.run_recovery.recover_expired()
    await container.chat_context.reconcile()


async def file_maintenance(container: Container) -> None:
    system = Principal(UUID(int=0), 'files-maintenance', True)
    try:
        await container.knowledge_documents.cleanup_uploads()
        for kind in ('reminders', 'expiry', 'purge', 'reconcile'):
            await container.file_jobs.run(system, kind)
    except AppError as error:
        if error.code != 'file_job_busy':
            raise
