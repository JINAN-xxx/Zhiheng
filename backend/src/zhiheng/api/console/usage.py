from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.billing.public import (
    TokenManagementService,
    TokenReconcile,
    UsageRecord,
    UsageSummary,
)

router = APIRouter(prefix='/api/usage', tags=['usage'])


@router.get('')
async def usage(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    user_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 100,
    source: Annotated[str | None, Query(max_length=24)] = None,
    status: Literal['pending', 'unknown', 'known'] | None = None,
) -> UsageSummary:
    target = user_id or principal.id
    if target != principal.id:
        principal.require_permission('ai:credits:read')
        await container.identity.directory_user(session, principal, target)
    result = await container.metering.summary(
        session,
        target,
        page=page,
        page_size=page_size,
        source=source,
        status=status,
    )
    result.points_enabled = (await container.credits.queries.state(session)).enabled
    return result


@router.post('/{run_id}/reconcile')
async def reconcile(
    run_id: UUID,
    body: TokenReconcile,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> UsageRecord:
    principal.require_permission('ai:credits:reconcile')
    async with session.begin():
        result = await TokenManagementService(container.metering).reconcile(session, principal, run_id, body)
        await container.operations.record(
            session,
            principal=principal,
            action='usage.reconcile',
            resource_type='run',
            resource_id=str(run_id),
            message='Token 未知用量已核对',
        )
        return result
