"""Read-only Console resource discovery from public domain delegation facts."""

from http import HTTPStatus
from typing import cast
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.access import ResourceAccessService
from zhiheng.modules.developer.public import ApplicationService, ResourceAction, ResourceKind
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class ResourceCandidate(BaseModel):
    kind: ResourceKind
    resource_id: UUID
    display_name: str
    status: str
    allowed_actions: list[ResourceAction]
    owned: bool


class BoundResource(ResourceCandidate):
    actions: list[ResourceAction]
    available: bool


class ResourceAccessInfo(BaseModel):
    application_version: int
    source_count: int
    source_bytes: int
    source_file_limit: int
    source_byte_limit: int
    can_bind_resources: bool
    can_configure_quota: bool
    items: list[BoundResource]


class DeveloperResourceQueries:
    def __init__(self, applications: ApplicationService, access: ResourceAccessService) -> None:
        self.applications, self.access = applications, access

    async def candidates(
        self, session: AsyncSession, principal: Principal, application_id: UUID, kind: ResourceKind
    ) -> list[ResourceCandidate]:
        await self.applications.require_owned(session, principal.id, application_id, lock=False)
        personal = self.access.personal(principal)
        metadata: list[tuple[UUID, str, str]]
        if kind == 'file':
            metadata = [
                (row.id, row.display_name, row.status)
                for row in await self.access.files.delegable_metadata(session, personal)
            ]
        elif kind == 'knowledge_base':
            metadata = [
                (row.id, row.name, row.index_status)
                for row in await self.access.knowledge.delegable_metadata(session, personal)
            ]
        else:
            metadata = [
                (row.id, row.name, row.status)
                for row in await self.access.skills.delegable_metadata(session, principal.id)
            ]
        result = []
        for resource_id, name, status in metadata:
            try:
                facts = await self.access.domain_actions(session, personal, kind, resource_id, lock=False, binding=True)
            except AppError as error:
                if error.status_code != HTTPStatus.NOT_FOUND:
                    raise
                continue
            result.append(
                ResourceCandidate(
                    kind=kind,
                    resource_id=resource_id,
                    display_name=name,
                    status=status,
                    allowed_actions=sorted(facts.actions),
                    owned=facts.ownership.owner_id == principal.id,
                )
            )
        return result

    async def summary(self, session: AsyncSession, principal: Principal, application_id: UUID) -> ResourceAccessInfo:
        app = self.applications.info(await self.applications.require(session, principal, application_id))
        owned = app.owner_id == principal.id
        count, size = await self.access.grants.source_usage(session, application_id)
        rows: list[BoundResource] = []
        if owned:
            # The same current ACL checks used by resource requests run before reading labels.
            candidates = {
                (row.kind, row.resource_id): row
                for kind in ('file', 'knowledge_base', 'skill')
                for row in await self.candidates(session, principal, application_id, cast('ResourceKind', kind))
            }
            for grant in await self.access.grants.list(session, principal.id, application_id):
                candidate = candidates.get((grant.kind, grant.resource_id))
                if candidate is not None:
                    try:
                        facts = await self.access.domain_actions(
                            session, principal, grant.kind, grant.resource_id, lock=False
                        )
                        if facts.ownership != grant.ownership:
                            candidate = None
                    except AppError as error:
                        if error.status_code != HTTPStatus.NOT_FOUND:
                            raise
                        candidate = None
                rows.append(
                    BoundResource(
                        kind=grant.kind,
                        resource_id=grant.resource_id,
                        actions=list(grant.actions),
                        display_name=candidate.display_name if candidate else '资源已失效',
                        status=candidate.status if candidate else 'unavailable',
                        allowed_actions=candidate.allowed_actions if candidate else [],
                        owned=candidate.owned if candidate else False,
                        available=candidate is not None,
                    )
                )
        return ResourceAccessInfo(
            application_version=app.version,
            source_count=count,
            source_bytes=size,
            source_file_limit=app.source_file_limit,
            source_byte_limit=app.source_byte_limit,
            can_bind_resources=owned and app.status == 'active',
            can_configure_quota=principal.is_admin,
            items=rows,
        )
