"""Bearer resource transport for native personal knowledge use cases."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from zhiheng.api.common.dependencies import ContainerDependency
from zhiheng.api.common.pagination import decode_cursor, encode_cursor
from zhiheng.api.common.preconditions import expected_version, idempotency_header
from zhiheng.api.resources.dependencies import resource_caller
from zhiheng.api.resources.schemas.knowledge import (
    BasePage,
    CreateRequest,
    DocumentPage,
    FolderPage,
    FolderPatchRequest,
    ImportRequest,
    PatchRequest,
    QueryBody,
)
from zhiheng.application.access import CallerContext
from zhiheng.application.operations.contracts import OperationInfo
from zhiheng.modules.knowledge.public import (
    BaseCreate,
    BaseInfo,
    BasePatch,
    BuildSelection,
    ChunkParameters,
    FolderCreate,
    FolderInfo,
    FolderPatch,
    QueryRequest,
)

router = APIRouter(prefix='/api/v1/knowledge-bases', tags=['resources:knowledge'])
ReadCaller = Annotated[CallerContext, Depends(resource_caller('knowledge:read'))]
WriteCaller = Annotated[CallerContext, Depends(resource_caller('knowledge:write'))]
QueryCaller = Annotated[CallerContext, Depends(resource_caller('knowledge:query'))]
Idempotency = Annotated[UUID, Header(alias='Idempotency-Key')]
IfMatch = Annotated[str | None, Header(alias='If-Match')]
Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=1024)]


def accepted(response: Response, operation: OperationInfo) -> OperationInfo:
    response.headers['Location'] = f'/api/v1/operations/{operation.id}'
    return operation


@router.post(
    '', status_code=201, openapi_extra={'x-response-headers': ['ETag'], 'x-required-scopes': ['knowledge:write']}
)
async def create(
    body: CreateRequest,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Idempotency,
) -> BaseInfo:
    idempotency_header(request)
    info = await container.knowledge_commands.create(caller, BaseCreate(**body.model_dump()), idempotency_key)
    response.headers['ETag'] = f'"knowledge-base-{info.version}"'
    response.headers['Location'] = f'/api/v1/knowledge-bases/{info.id}'
    return info


@router.get('', openapi_extra={'x-required-scopes': ['knowledge:read']})
async def page(
    container: ContainerDependency, caller: ReadCaller, limit: Limit = 20, cursor: Cursor = None
) -> BasePage:
    assert caller.identity is not None
    binding = f'{caller.identity.application_id}:knowledge-base'
    found = await container.knowledge_queries.page(caller, decode_cursor(cursor, binding), limit + 1)
    items, more = found[:limit], len(found) > limit
    return BasePage(
        items=items,
        has_more=more,
        next_cursor=encode_cursor(binding, items[-1].created_at, items[-1].id) if more else None,
    )


@router.get('/{base_id}', openapi_extra={'x-response-headers': ['ETag'], 'x-required-scopes': ['knowledge:read']})
async def get(base_id: UUID, response: Response, container: ContainerDependency, caller: ReadCaller) -> BaseInfo:
    info = await container.knowledge_queries.get(caller, base_id)
    response.headers['ETag'] = f'"knowledge-base-{info.version}"'
    return info


@router.patch('/{base_id}', openapi_extra={'x-response-headers': ['ETag'], 'x-required-scopes': ['knowledge:write']})
async def patch(
    base_id: UUID,
    body: PatchRequest,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    if_match: IfMatch = None,
) -> BaseInfo:
    info = await container.knowledge_commands.patch(
        caller, base_id, BasePatch(expected_version=expected_version(request, 'knowledge-base'), **body.model_dump())
    )
    response.headers['ETag'] = f'"knowledge-base-{info.version}"'
    return info


@router.get('/{base_id}/folders', openapi_extra={'x-required-scopes': ['knowledge:read']})
async def folders(
    base_id: UUID, container: ContainerDependency, caller: ReadCaller, limit: Limit = 20, cursor: Cursor = None
) -> FolderPage:
    assert caller.identity is not None
    binding = f'{caller.identity.application_id}:knowledge-base:{base_id}:folder'
    after = decode_cursor(cursor, binding)
    found = sorted(
        (
            item
            for item in await container.knowledge_queries.folders(caller, base_id)
            if after is None or (item.created_at, item.id) < after
        ),
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    items, more = found[:limit], len(found) > limit
    return FolderPage(
        items=items,
        has_more=more,
        next_cursor=encode_cursor(binding, items[-1].created_at, items[-1].id) if more else None,
    )


@router.post(
    '/{base_id}/folders',
    status_code=201,
    openapi_extra={'x-response-headers': ['ETag'], 'x-required-scopes': ['knowledge:write']},
)
async def create_folder(
    base_id: UUID,
    body: FolderCreate,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Idempotency,
) -> FolderInfo:
    idempotency_header(request)
    info = await container.knowledge_commands.folder_create(caller, base_id, body, idempotency_key)
    response.headers['ETag'] = f'"knowledge-folder-{info.version}"'
    response.headers['Location'] = f'/api/v1/knowledge-bases/{base_id}/folders/{info.id}'
    return info


@router.patch(
    '/{base_id}/folders/{folder_id}',
    openapi_extra={'x-response-headers': ['ETag'], 'x-required-scopes': ['knowledge:write']},
)
async def patch_folder(
    base_id: UUID,
    folder_id: UUID,
    body: FolderPatchRequest,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    if_match: IfMatch = None,
) -> FolderInfo:
    info = await container.knowledge_commands.folder_patch(
        caller,
        base_id,
        folder_id,
        FolderPatch(expected_version=expected_version(request, 'knowledge-folder'), name=body.name),
    )
    response.headers['ETag'] = f'"knowledge-folder-{info.version}"'
    return info


@router.get('/{base_id}/documents', openapi_extra={'x-required-scopes': ['knowledge:read']})
async def documents(
    base_id: UUID, container: ContainerDependency, caller: ReadCaller, limit: Limit = 20, cursor: Cursor = None
) -> DocumentPage:
    assert caller.identity is not None
    binding = f'{caller.identity.application_id}:knowledge-base:{base_id}:document'
    after = decode_cursor(cursor, binding)
    found = sorted(
        (
            item
            for item in await container.knowledge_queries.documents(caller, base_id)
            if after is None or (item.created_at, item.id) < after
        ),
        key=lambda item: (item.created_at, item.id),
        reverse=True,
    )
    items, more = found[:limit], len(found) > limit
    return DocumentPage(
        items=items,
        has_more=more,
        next_cursor=encode_cursor(binding, items[-1].created_at, items[-1].id) if more else None,
    )


@router.post(
    '/{base_id}/documents', status_code=202, openapi_extra={'x-required-scopes': ['knowledge:write', 'files:read']}
)
async def import_documents(
    base_id: UUID,
    body: ImportRequest,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Idempotency,
) -> OperationInfo:
    idempotency_header(request)
    return accepted(
        response,
        await container.knowledge_commands.job(
            caller, base_id, 'import', idempotency_key, selection=BuildSelection(**body.model_dump())
        ),
    )


@router.post('/{base_id}/query', status_code=202, openapi_extra={'x-required-scopes': ['knowledge:query']})
async def query(
    base_id: UUID,
    body: QueryBody,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: QueryCaller,
    idempotency_key: Idempotency,
) -> OperationInfo:
    idempotency_header(request)
    return accepted(
        response,
        await container.knowledge_commands.query(
            caller, base_id, QueryRequest(client_request_id=uuid4(), **body.model_dump()), idempotency_key
        ),
    )


@router.post(
    '/{base_id}/rebuild',
    status_code=202,
    openapi_extra={'x-transport-policy': 'bodyless', 'x-required-scopes': ['knowledge:write']},
)
async def rebuild(
    base_id: UUID,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Idempotency,
    if_match: IfMatch = None,
) -> OperationInfo:
    idempotency_header(request)
    return accepted(
        response,
        await container.knowledge_commands.job(
            caller, base_id, 'rebuild', idempotency_key, expected_version=expected_version(request, 'knowledge-base')
        ),
    )


@router.post(
    '/{base_id}/documents/{document_id}/rechunk',
    status_code=202,
    openapi_extra={'x-required-scopes': ['knowledge:write']},
)
async def rechunk(
    base_id: UUID,
    document_id: UUID,
    body: ChunkParameters,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Idempotency,
    if_match: IfMatch = None,
) -> OperationInfo:
    idempotency_header(request)
    return accepted(
        response,
        await container.knowledge_commands.job(
            caller,
            base_id,
            'rechunk',
            idempotency_key,
            selection=BuildSelection(**body.model_dump()),
            expected_version=expected_version(request, 'knowledge-document'),
            child_id=document_id,
        ),
    )


@router.delete(
    '/{base_id}/documents/{document_id}',
    status_code=202,
    openapi_extra={'x-transport-policy': 'bodyless', 'x-required-scopes': ['knowledge:write']},
)
async def remove_document(
    base_id: UUID,
    document_id: UUID,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Idempotency,
    if_match: IfMatch = None,
) -> OperationInfo:
    idempotency_header(request)
    return accepted(
        response,
        await container.knowledge_commands.job(
            caller,
            base_id,
            'delete_document',
            idempotency_key,
            expected_version=expected_version(request, 'knowledge-document'),
            child_id=document_id,
        ),
    )


@router.delete(
    '/{base_id}/folders/{folder_id}',
    status_code=202,
    openapi_extra={'x-transport-policy': 'bodyless', 'x-required-scopes': ['knowledge:write']},
)
async def remove_folder(
    base_id: UUID,
    folder_id: UUID,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Idempotency,
    if_match: IfMatch = None,
) -> OperationInfo:
    idempotency_header(request)
    return accepted(
        response,
        await container.knowledge_commands.job(
            caller,
            base_id,
            'delete_folder',
            idempotency_key,
            expected_version=expected_version(request, 'knowledge-folder'),
            child_id=folder_id,
        ),
    )


@router.delete(
    '/{base_id}',
    status_code=202,
    openapi_extra={'x-transport-policy': 'bodyless', 'x-required-scopes': ['knowledge:write']},
)
async def remove_base(
    base_id: UUID,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Idempotency,
    if_match: IfMatch = None,
) -> OperationInfo:
    idempotency_header(request)
    return accepted(
        response,
        await container.knowledge_commands.job(
            caller,
            base_id,
            'delete_base',
            idempotency_key,
            expected_version=expected_version(request, 'knowledge-base'),
        ),
    )
