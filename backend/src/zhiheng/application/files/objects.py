"""Compensate fresh object writes using committed file registration as authority."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import FileService

logger = logging.getLogger(__name__)


class FileObjects:
    def __init__(self, database: Database, files: FileService) -> None:
        self.database, self.files = database, files

    @asynccontextmanager
    async def registration(self, storage_key: str) -> AsyncIterator[AsyncSession]:
        """Own registration and its outcome; an uncertain commit is left to reconciliation."""
        body_finished = False
        uncertain = False
        cancelled = False
        session: AsyncSession | None = None
        try:
            async with self.database.sessions.begin() as session:
                yield session
                body_finished = True
        except BaseException as error:
            # Cancellation of SQLAlchemy's shielded context-manager exit can
            # arrive before its commit task finishes. A SELECT returning no row
            # at that instant cannot prove that the object is unregistered.
            cancelled = isinstance(error, asyncio.CancelledError)
            uncertain = body_finished or cancelled
            raise
        finally:
            # SQLAlchemy's sessionmaker exit may raise from its transaction
            # hook before closing the session. A cancelled exit can still be
            # committing, so only the completed exit is safe to close here.
            if session is not None and not cancelled:
                await session.close()
            if uncertain:
                logger.warning('Deferred file object cleanup: transaction outcome uncertain')
            else:
                await self.discard_unregistered(storage_key)

    async def discard_unregistered(self, storage_key: str) -> None:
        """Only the fresh key owned by the current attempt may be passed here."""
        try:
            async with self.database.sessions() as session:
                registered = await self.files.object_registered(session, storage_key)
            if not registered:
                await self.files.storage.delete(storage_key)
        except Exception as error:
            # A lost commit acknowledgement does not prove a rollback. Leave
            # uncertain registration or IO to native reconciliation.
            logger.warning('Deferred file object cleanup (%s)', type(error).__name__)
