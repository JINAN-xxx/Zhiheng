"""Recover expired execution attempts and their usage/projections atomically."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zhiheng.application.execution.lifecycle import RunLifecycle
from zhiheng.modules.billing.public import UsageService
from zhiheng.runtime.runs import RunStore

RECOVERY_LIMIT = 100


class RunRecovery:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        runs: RunStore,
        usage: UsageService,
        lifecycle: RunLifecycle,
    ) -> None:
        self.sessions, self.runs, self.usage, self.lifecycle = sessions, runs, usage, lifecycle

    async def recover_expired(self, *, limit: int = RECOVERY_LIMIT) -> None:
        # Release each run's Key/account locks before acquiring another run's Key.
        # Its interrupted state, usage outcome and projections commit atomically.
        for _ in range(limit):
            async with self.sessions.begin() as session:
                recovered = await self.runs.recover_one(session)
                if recovered is None:
                    break
                run_id, provider_started = recovered
                await self.usage.settle(session, run_id, rejected_before_execution=not provider_started)
                await self.lifecycle.project(session, await self.runs.projection(session, run_id))
        await self.recover_failed_chat_holds(limit=limit)

    async def recover_failed_chat_holds(self, *, limit: int = RECOVERY_LIMIT) -> int:
        """Repair eligible historical failures without revisiting their conversations."""
        async with self.sessions() as session:
            candidates = await self.usage.failed_chat_waivers(session, limit=limit)
        released = 0
        for run_id in candidates:
            async with self.sessions.begin() as session:
                released += int(await self.usage.waive_failed_chat(session, run_id))
        return released
