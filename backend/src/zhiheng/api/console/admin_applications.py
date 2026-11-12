"""Platform application controls expose quotas and switches, never personal contents or keys."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.developer.public import (
    ApplicationGovernanceInfo,
    ApplicationGovernancePage,
    ApplicationGovernanceState,
    ResourceQuotaRequest,
)

router = APIRouter(prefix='/api/admin/applications', tags=['admin-applications'])


@router.get('')
async def applications(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    owner_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApplicationGovernancePage:
    return await container.applications.governance_list(
        session, principal, owner_id=owner_id, page=page, page_size=page_size
    )


@router.patch('/{application_id}/state')
async def state(
    application_id: UUID,
    body: ApplicationGovernanceState,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ApplicationGovernanceInfo:
    async with session.begin():
        result = await container.applications.governance_state(
            session, principal, application_id, enabled=body.enabled, expected_version=body.expected_version
        )
        await container.operations.record(
            session,
            principal=principal,
            action='applications.platform_state',
            resource_type='applications',
            resource_id=str(application_id),
            message='应用平台状态已调整',
        )
        return result


@router.patch('/{application_id}/resource-quota')
async def quota(
    application_id: UUID,
    body: ResourceQuotaRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict[str, int]:
    async with session.begin():
        version = await container.resource_grants.configure_governance_quota(
            session,
            principal,
            application_id,
            file_limit=body.source_file_limit,
            byte_limit=body.source_byte_limit,
            expected_version=body.expected_version,
        )
        await container.operations.record(
            session,
            principal=principal,
            action='applications.resource_quota',
            resource_type='applications',
            resource_id=str(application_id),
            message='应用资源额度已调整',
        )
        return {'version': version}
