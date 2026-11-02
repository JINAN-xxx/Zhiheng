"""Platform skill transport; native imports and immutable revisions remain authoritative."""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from zhiheng.api.common.dependencies import ContainerDependency
from zhiheng.api.common.pagination import decode_cursor, encode_cursor
from zhiheng.api.common.preconditions import expected_version, idempotency_header
from zhiheng.api.resources.dependencies import resource_caller
from zhiheng.api.resources.schemas.skills import ImportRequest, RevisionPage, SkillPage, StatusRequest
from zhiheng.application.access import CallerContext
from zhiheng.modules.skills.public import DraftInfo, ResourceSkillInfo, RevisionInfo

router = APIRouter(prefix='/api/v1', tags=['resources:skills'])
ReadCaller = Annotated[CallerContext, Depends(resource_caller('skills:read'))]
WriteCaller = Annotated[CallerContext, Depends(resource_caller('skills:write'))]


@router.post('/skill-imports', status_code=201, openapi_extra={'x-required-scopes': ['skills:write', 'files:read']})
async def prepare(
    body: ImportRequest,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Annotated[UUID, Header(alias='Idempotency-Key')],
) -> DraftInfo:
    idempotency_header(request)
    result = await container.skill_commands.prepare(caller, body.file_id, idempotency_key)
    response.headers['Location'] = f'/api/v1/skill-imports/{result.draft_id}/confirm'
    return result


@router.post(
    '/skill-imports/{draft_id}/confirm',
    status_code=201,
    openapi_extra={
        'x-transport-policy': 'bodyless',
        'x-response-headers': ['ETag'],
        'x-required-scopes': ['skills:write', 'files:read'],
    },
)
async def confirm(
    draft_id: UUID,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    idempotency_key: Annotated[UUID, Header(alias='Idempotency-Key')],
) -> ResourceSkillInfo:
    idempotency_header(request)
    result = await container.skill_commands.confirm(caller, draft_id, idempotency_key)
    response.headers['Location'] = f'/api/v1/skills/{result.id}'
    response.headers['ETag'] = f'"skill-{result.resource_version}"'
    return result


@router.get('/skills', openapi_extra={'x-required-scopes': ['skills:read']})
async def page(
    container: ContainerDependency,
    caller: ReadCaller,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> SkillPage:
    assert caller.identity is not None
    binding = f'{caller.identity.application_id}:skill'
    found = await container.skill_queries.page(caller, decode_cursor(cursor, binding), limit + 1)
    items, has_more = found[:limit], len(found) > limit
    return SkillPage(
        items=items,
        has_more=has_more,
        next_cursor=encode_cursor(binding, items[-1].created_at, items[-1].id) if has_more else None,
    )


@router.get('/skills/{skill_id}', openapi_extra={'x-response-headers': ['ETag'], 'x-required-scopes': ['skills:read']})
async def get(
    skill_id: UUID, response: Response, container: ContainerDependency, caller: ReadCaller
) -> ResourceSkillInfo:
    info = await container.skill_queries.get(caller, skill_id)
    response.headers['ETag'] = f'"skill-{info.resource_version}"'
    return info


@router.patch(
    '/skills/{skill_id}', openapi_extra={'x-response-headers': ['ETag'], 'x-required-scopes': ['skills:write']}
)
async def status(
    skill_id: UUID,
    body: StatusRequest,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    if_match: Annotated[str | None, Header(alias='If-Match')] = None,
) -> ResourceSkillInfo:
    info = await container.skill_commands.mutate(
        caller, skill_id, expected_version(request, 'skill'), status=body.status
    )
    response.headers['ETag'] = f'"skill-{info.resource_version}"'
    return info


@router.delete(
    '/skills/{skill_id}',
    openapi_extra={
        'x-transport-policy': 'bodyless',
        'x-response-headers': ['ETag'],
        'x-required-scopes': ['skills:write'],
    },
)
async def remove(
    skill_id: UUID,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    if_match: Annotated[str | None, Header(alias='If-Match')] = None,
) -> ResourceSkillInfo:
    info = await container.skill_commands.mutate(caller, skill_id, expected_version(request, 'skill'))
    response.headers['ETag'] = f'"skill-{info.resource_version}"'
    return info


@router.get('/skills/{skill_id}/versions', openapi_extra={'x-required-scopes': ['skills:read']})
async def versions(
    skill_id: UUID,
    container: ContainerDependency,
    caller: ReadCaller,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> RevisionPage:
    assert caller.identity is not None
    binding = f'{caller.identity.application_id}:skill:{skill_id}:revision'
    after = decode_cursor(cursor, binding)
    found = await container.skill_queries.versions(caller, skill_id, after=after, limit=limit + 1)
    items, has_more = found[:limit], len(found) > limit
    return RevisionPage(
        items=items,
        has_more=has_more,
        next_cursor=encode_cursor(binding, items[-1].created_at, items[-1].id) if has_more else None,
    )


@router.get('/skills/{skill_id}/versions/{revision_id}', openapi_extra={'x-required-scopes': ['skills:read']})
async def revision(
    skill_id: UUID, revision_id: UUID, container: ContainerDependency, caller: ReadCaller
) -> RevisionInfo:
    return await container.skill_queries.revision(caller, skill_id, revision_id)


@router.get(
    '/skills/{skill_id}/versions/{revision_id}/content',
    response_class=Response,
    responses={
        200: {
            'content': {
                'application/zip': {'schema': {'type': 'string', 'format': 'binary'}},
                'text/markdown': {'schema': {'type': 'string'}},
            },
            'headers': {'ETag': {'schema': {'type': 'string'}, 'description': '当时保存的原始包 SHA256。'}},
        }
    },
    openapi_extra={'x-required-scopes': ['skills:read']},
)
async def content(skill_id: UUID, revision_id: UUID, container: ContainerDependency, caller: ReadCaller) -> Response:
    result = await container.skill_queries.content(caller, skill_id, revision_id)
    return Response(
        result.content,
        media_type='application/zip' if result.info.filename.lower().endswith('.zip') else 'text/markdown',
        headers={
            'ETag': f'"{result.info.package_sha}"',
            'Content-Disposition': f"attachment; filename=download; filename*=UTF-8''{quote(result.info.filename, safe='')}",
            'Content-Security-Policy': "default-src 'none'; sandbox",
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, no-store',
        },
    )
