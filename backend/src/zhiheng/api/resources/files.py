"""Platform file HTTP adapter. Ownership, transactions and storage remain in use cases."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, Security, UploadFile
from fastapi.security import HTTPAuthorizationCredentials

from zhiheng.api.common.dependencies import ContainerDependency, SessionDependency
from zhiheng.api.common.file_content import file_content_response
from zhiheng.api.common.pagination import MAX_CURSOR_LENGTH, decode_cursor, encode_cursor
from zhiheng.api.common.preconditions import Idempotency
from zhiheng.api.resources.dependencies import resource_bearer, resource_caller
from zhiheng.api.resources.schemas.files import ResourceFilePage
from zhiheng.application.access import CallerContext
from zhiheng.modules.files.public import FileInfo
from zhiheng.shared.errors import AppError

MAX_VERSION_DIGITS = 10
MULTIPART_FIELD_COUNT = 2
SEALED_FILE_CURSOR = 'f1.'

router = APIRouter(prefix='/api/v1/files', tags=['resources:files'])
ReadCaller = Annotated[CallerContext, Depends(resource_caller('files:read'))]
WriteCaller = Annotated[CallerContext, Depends(resource_caller('files:write'))]
Purpose = Annotated[str, Form(min_length=1, max_length=64, pattern=r'^[a-z][a-z0-9_-]*$')]


_download_auth = resource_caller('files:read')


async def download_caller(
    file_id: UUID,
    request: Request,
    container: ContainerDependency,
    session: SessionDependency,
    security: Annotated[HTTPAuthorizationCredentials | None, Security(resource_bearer)] = None,
) -> CallerContext:
    try:
        return await _download_auth(request, container, session, security)
    except AppError as error:
        async with container.database.sessions.begin() as audit:
            await container.files.record_access(
                audit,
                None,
                file_id,
                outcome='denied',
                bytes_sent=0,
                range_header=request.headers.get('range'),
                error_code=error.code,
            )
        raise


DownloadCaller = Annotated[CallerContext, Depends(download_caller)]


def etag(info: FileInfo) -> str:
    return f'"file-{info.version}"'


def expected_version(request: Request) -> int:
    values = request.headers.getlist('if-match')
    if not values:
        raise AppError('precondition_required', '必须提供If-Match文件版本', 428)
    value = values[0]
    if len(values) != 1 or not value.startswith('"file-') or not value.endswith('"'):
        raise AppError('invalid_precondition', '需要强文件版本ETag', 422)
    version = value[6:-1]
    if not version.isascii() or not version.isdigit() or len(version) > MAX_VERSION_DIGITS or int(version) < 1:
        raise AppError('invalid_precondition', '文件版本ETag无效', 422)
    return int(version)


@router.post(
    '',
    status_code=201,
    openapi_extra={
        'x-transport-policy': 'multipart_file',
        'x-response-headers': ['ETag'],
        'x-required-scopes': ['files:write'],
    },
)
async def upload(
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    file: Annotated[UploadFile, File(json_schema_extra={'format': 'binary'})],
    purpose: Purpose,
    idempotency_key: Idempotency,
) -> FileInfo:
    form = await request.form()
    fields = form.multi_items()
    if len(fields) != MULTIPART_FIELD_COUNT or sorted(name for name, _ in fields) != ['file', 'purpose']:
        raise AppError('invalid_multipart', '仅允许一个file和一个purpose字段', 422)
    content = await file.read(20 * 1024 * 1024 + 1)
    info = await container.file_commands.upload(
        caller.principal,
        filename=file.filename or '',
        content=content,
        purpose=purpose,
        caller=caller,
        idempotency_key=idempotency_key,
    )
    response.headers['ETag'] = etag(info)
    response.headers['Location'] = f'/api/v1/files/{info.id}'
    return info


@router.get('', openapi_extra={'x-required-scopes': ['files:read']})
async def page(
    container: ContainerDependency,
    caller: ReadCaller,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
    purpose: Annotated[str | None, Query(min_length=1, max_length=64, pattern=r'^[a-z][a-z0-9_-]*$')] = None,
) -> ResourceFilePage:
    assert caller.identity is not None
    binding = f'{caller.identity.application_id}:file:purpose={purpose or ""}'
    if cursor is not None and cursor.startswith(SEALED_FILE_CURSOR):
        try:
            cursor = container.cipher.decrypt(cursor.removeprefix(SEALED_FILE_CURSOR))
        except AppError as error:
            if error.code != 'credential_unavailable':
                raise
            raise AppError('invalid_cursor', '分页游标无效或不属于当前应用及过滤条件', 422) from error
    after = decode_cursor(cursor, binding)
    found = await container.file_queries.page(caller, purpose=purpose, after=after, limit=limit)
    position = found.next_position
    next_cursor = None
    if position is not None:
        next_cursor = encode_cursor(binding, *position)
        # The legacy format exposes its position. Seal positions not authorized for the response.
        if not found.items or position != (found.items[-1].created_at, found.items[-1].id):
            next_cursor = SEALED_FILE_CURSOR + container.cipher.encrypt(next_cursor)
    return ResourceFilePage(items=found.items, next_cursor=next_cursor, has_more=position is not None)


@router.get('/{file_id}', openapi_extra={'x-response-headers': ['ETag'], 'x-required-scopes': ['files:read']})
async def get(file_id: UUID, response: Response, container: ContainerDependency, caller: ReadCaller) -> FileInfo:
    info = await container.file_queries.get(caller, file_id)
    response.headers['ETag'] = etag(info)
    return info


@router.get(
    '/{file_id}/content',
    response_class=Response,
    openapi_extra={'x-supports-range': True, 'x-required-scopes': ['files:read']},
)
async def content(
    file_id: UUID,
    request: Request,
    container: ContainerDependency,
    caller: DownloadCaller,
    range_header: Annotated[str | None, Header(alias='Range')] = None,
) -> Response:
    try:
        info, data = await container.file_queries.read(caller.principal, file_id, caller=caller)
        return await file_content_response(request, container, caller.principal, info, data, resource=True)
    except AppError as error:
        async with container.database.sessions.begin() as session:
            await container.files.record_access(
                session,
                caller.principal,
                file_id,
                outcome='denied',
                bytes_sent=0,
                range_header=request.headers.get('range'),
                error_code=error.code,
            )
        raise


@router.delete(
    '/{file_id}',
    openapi_extra={
        'x-transport-policy': 'bodyless',
        'x-response-headers': ['ETag'],
        'x-required-scopes': ['files:write'],
    },
)
async def remove(
    file_id: UUID,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    if_match: Annotated[str | None, Header(alias='If-Match')] = None,
) -> FileInfo:
    info = await container.file_commands.remove(caller.principal, file_id, expected_version(request), caller=caller)
    response.headers['ETag'] = etag(info)
    return info


@router.post(
    '/{file_id}/restore',
    openapi_extra={
        'x-transport-policy': 'bodyless',
        'x-response-headers': ['ETag'],
        'x-required-scopes': ['files:write'],
    },
)
async def restore(
    file_id: UUID,
    request: Request,
    response: Response,
    container: ContainerDependency,
    caller: WriteCaller,
    if_match: Annotated[str | None, Header(alias='If-Match')] = None,
) -> FileInfo:
    info = await container.file_commands.remove(
        caller.principal, file_id, expected_version(request), restore=True, caller=caller
    )
    response.headers['ETag'] = etag(info)
    return info
