"""Domain projections follow Run state changes in the same transaction."""

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot


class RunLifecycle:
    def __init__(self, chat: ChatService, work: WorkService | None = None) -> None:
        self.chat, self.work = chat, work or WorkService()

    async def project(self, session: AsyncSession, snapshot: RunSnapshot) -> None:
        if snapshot.resource_type == 'work_turn':
            await self.work.project(
                session,
                snapshot.resource_id,
                snapshot.id,
                content=snapshot.text,
                reasoning=snapshot.reasoning,
                status=snapshot.status,
                usage=snapshot.usage.model_dump(),
                error=snapshot.error.model_dump() if snapshot.error else None,
                terminal=snapshot.status in TERMINAL_STATUSES,
            )
        elif snapshot.resource_type == 'chat' and snapshot.status == 'completed':
            await self.chat.complete(
                session,
                conversation_id=snapshot.resource_id,
                run_id=snapshot.id,
                content=snapshot.text,
                reasoning=snapshot.reasoning,
            )
