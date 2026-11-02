"""Current resource authorization surrounds storage IO without holding row locks."""

from datetime import datetime
from uuid import UUID

from zhiheng.application.access import CallerContext, ResourceAccessService
from zhiheng.infrastructure.database import Database
from zhiheng.modules.files.public import FileBinding, FileInfo, FileService, resource_page
from zhiheng.shared.principal import Principal


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
    ) -> list[FileInfo]:
        async with self.database.sessions.begin() as session:
            caller = await self.access.current(session, caller, 'files:read')
            assert caller.identity is not None
            grants = await self.access.grants.list(session, caller.principal.id, caller.identity.application_id, 'file')
            bindings = tuple(
                FileBinding(g.resource_id, g.ownership.owner_id, g.ownership.stamp)
                for g in grants
                if 'read' in g.actions
            )
            return await resource_page(
                self.files,
                session,
                self.access.personal(caller.principal),
                bindings,
                purpose=purpose,
                after=after,
                limit=limit,
            )

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
