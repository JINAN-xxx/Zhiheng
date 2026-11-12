"""Read and cancel the original native work through current resource authority."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from zhiheng.api.common.dependencies import ContainerDependency
from zhiheng.api.common.pagination import decode_cursor, encode_cursor
from zhiheng.api.resources.dependencies import resource_caller
from zhiheng.api.resources.schemas.operations import OperationPage
from zhiheng.application.access import CallerContext
from zhiheng.application.operations.contracts import OperationInfo

router = APIRouter(prefix='/api/v1/operations', tags=['resources:operations'])
Caller = Annotated[CallerContext, Depends(resource_caller(None))]
READ_SCOPES = {
    'x-required-scopes': ['knowledge:read', 'knowledge:query'],
    'x-required-scopes-mode': 'by-kind',
    'x-required-scopes-by-kind': {
        'knowledge_job': ['knowledge:read'],
        'knowledge_preparation': ['knowledge:query'],
    },
}


@router.get('', openapi_extra=READ_SCOPES)
async def page(
    container: ContainerDependency,
    caller: Caller,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> OperationPage:
    assert caller.identity is not None
    binding = f'{caller.identity.application_id}:operation'
    found = await container.operation_queries.page(caller, decode_cursor(cursor, binding), limit + 1)
    items, more = found[:limit], len(found) > limit
    return OperationPage(
        items=items,
        has_more=more,
        next_cursor=encode_cursor(binding, items[-1].created_at, items[-1].id) if more else None,
    )


@router.get('/{operation_id}', openapi_extra=READ_SCOPES)
async def get(operation_id: UUID, container: ContainerDependency, caller: Caller) -> OperationInfo:
    return await container.operation_queries.get(caller, operation_id)


@router.post(
    '/{operation_id}/cancel',
    status_code=202,
    openapi_extra={'x-transport-policy': 'bodyless', 'x-required-scopes': ['knowledge:write']},
)
async def cancel(
    operation_id: UUID, response: Response, container: ContainerDependency, caller: Caller
) -> OperationInfo:
    result = await container.operation_cancellation.cancel(caller, operation_id)
    response.headers['Location'] = f'/api/v1/operations/{operation_id}'
    return result
