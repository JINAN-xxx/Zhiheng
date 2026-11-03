from uuid import UUID

from zhiheng.application.inference import InferenceExecutor
from zhiheng.bootstrap.container import Container


async def execute_run(container: Container, run_id: UUID) -> None:
    executor = InferenceExecutor(
        sessions=container.database.sessions,
        runs=container.runs,
        usage=container.usage,
        chat=container.chat,
        provider_factory=container.prepare_provider,
        lease_seconds=container.settings.lease_seconds,
        timeout_seconds=container.settings.provider_timeout_seconds,
        before_provider=container.require_model_execution,
    )
    await executor.execute(run_id)
    await container.chat_context.reconcile()
