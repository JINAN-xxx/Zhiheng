from uuid import UUID

from zhiheng.bootstrap.container import Container


async def execute_run(container: Container, run_id: UUID) -> None:
    await container.run_executor.execute(run_id)
    await container.chat_context.reconcile()
