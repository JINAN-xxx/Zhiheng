"""A transaction gate between billing mode changes and new task admission."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MODE_GATE = 'zhiheng:billing:mode'


async def lock_billing_admission(session: AsyncSession) -> None:
    """Hold a shared lock until commit, before reading mode or creating parent work."""
    await session.execute(text('SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))'), {'key': MODE_GATE})


async def lock_billing_switch(session: AsyncSession) -> None:
    """Exclude new admission while checking quiescence and changing the mode."""
    await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))'), {'key': MODE_GATE})
