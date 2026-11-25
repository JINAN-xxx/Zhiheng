"""Current resource authorization surrounds storage IO without holding row locks."""

from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from uuid import UUID

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import FileBinding, FileInfo, FileService, resource_page
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MAX_FILE_PAGE_BATCHES = 4


@dataclass(frozen=True)
class FileResourcePage:
    items: list[FileInfo]
    next_position: tuple[datetime, UUID] | None


class FileQueries:
    def __init__(self, database: Database, files: FileService, access: ResourceAccessService) -> None:
        self.database, self.files, self.access = database, files, access

    async def get(self, caller: CallerContext, file_id: UUID) -> FileInfo:
        async with self.database.sessions.begin() as session:
            await self.access.require(session, caller, 'file', file_id, 'read')
            return await self.files.get(session, self.access.personal(caller.principal), file_id)

    async def page(
        self,
        caller: CallerContext,
        *,
        purpose: str | None,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> FileResourcePage:
        async with self.database.sessions.begin() as session:
            caller = await self.access.current(session, caller, 'files:read')
            grants = await self.access.readable_grants(session, caller, 'file')
            bindings = tuple(FileBinding(g.resource_id, g.ownership.owner_id, g.ownership.stamp) for g in grants)
            principal = self.access.personal(caller.principal)
            position = after
            items: list[FileInfo] = []
            batch_size = limit + 1
            for batch in range(MAX_FILE_PAGE_BATCHES):
                # The final fetch peeks one row past the scan budget without consuming it.
                take = batch_size + (batch == MAX_FILE_PAGE_BATCHES - 1)
                candidates = await resource_page(
                    self.files, session, principal, bindings, purpose=purpose, after=position, limit=take
                )
                for item in candidates[:batch_size]:
                    try:
                        await self.files.ensure_readable(session, principal, item.id)
                    except AppError as error:
                        if error.status_code != HTTPStatus.NOT_FOUND:
                            raise
                    else:
                        if len(items) == limit:
                            return FileResourcePage(items, position)
                        items.append(item)
                    position = (item.created_at, item.id)
                if len(candidates) < take:
                    return FileResourcePage(items, None)
            return FileResourcePage(items, position)

    async def read(
        self,
        principal: Principal,
        file_id: UUID,
        *,
        caller: CallerContext | None = None,
    ) -> tuple[FileInfo, bytes]:
        async with self.database.sessions.begin() as session:
            if caller is not None:
                await self.access.require(session, caller, 'file', file_id, 'read')
                principal = self.access.personal(caller.principal)
            ticket = await self.files.read_ticket(session, principal, file_id)
        content = await self.files.read_content(ticket)
        # A slow read must not return content after a concurrent Key/App/ACL revocation.
        async with self.database.sessions.begin() as session:
            if caller is not None:
                await self.access.require(session, caller, 'file', file_id, 'read')
            current = await self.files.read_ticket(session, principal, file_id)
        return current.info, content
