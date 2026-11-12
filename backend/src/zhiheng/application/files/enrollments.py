"""Release purged file enrollments without coupling maintenance to process assembly."""

from uuid import UUID

from zhiheng.application.access import ResourceAccessService
from zhiheng.infrastructure.database import Database
from zhiheng.modules.developer.public import ResourceGrantService


class FileEnrollmentRelease:
    def __init__(self, database: Database, grants: ResourceGrantService, access: ResourceAccessService) -> None:
        self.database, self.grants, self.access = database, grants, access

    async def release(self, file_id: UUID) -> None:
        async with self.database.sessions() as session:
            applications = await self.grants.enrolled_applications(session, file_id)
        for application_id in applications:
            async with self.database.sessions.begin() as session:
                await self.access.release_purged(session, application_id, file_id)
