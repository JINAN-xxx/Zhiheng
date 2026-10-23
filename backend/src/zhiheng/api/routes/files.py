from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Path, Query, Request, Response

from zhiheng.api.contracts import EmptyRequest
from zhiheng.api.dependencies import COOKIE_NAME, ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.api.file_download import AuditedFileResponse
from zhiheng.application.file_inputs import ParsedFile
from zhiheng.modules.files.public import (
    AclInfo,
    AclRequest,
    AuditInfo,
    FileAuditPage,
    FileAuditQuery,
    FileInfo,
    FilePage,
    FilePatch,
    FileQuery,
    FileStats,
    IssueAction,
    JobInfo,
    JobKind,
    PolicyInfo,
    PolicyRequest,
    ReconcileIssueInfo,
    ReferenceInfo,
    ReminderAction,
    ReminderInfo,
    RetentionPolicyInfo,
    RetentionPolicyPatch,
    RetentionPolicyRequest,
    TransferRequest,
    VersionRequest,
    validate_filename,
)
from zhiheng.modules.identity.public import IdentityInfo
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/files', tags=['files'])


@router.post('', status_code=201)
async def upload(
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> FileInfo:
    validate_filename(filename)
    content = await request.body()
    key = await container.files.storage.put(content)
    try:
        async with session.begin():
            return await container.files.create(session, principal, filename=filename, content=content, storage_key=key)
    except BaseException:
        await container.files.storage.delete(key)
        raise


@router.get('')
async def files(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    filters: Annotated[FileQuery, Query()],
) -> FilePage:
    return await container.files.list_files(session, principal, filters)


@router.get('/stats')
async def stats(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    filters: Annotated[FileQuery, Query()],
) -> FileStats:
    return await container.files.stats(session, principal, filters)


@router.get('/users')
async def users(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    search: Annotated[str, Query(max_length=80)] = '',
) -> list[IdentityInfo]:
    principal.require_admin()
    return await container.identity.directory(session, search=search)


@router.get('/policy')
async def policy(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> PolicyInfo:
    principal.require_admin()
    return await container.files.policy(session)


@router.patch('/policy')
async def update_policy(
    body: PolicyRequest, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> PolicyInfo:
    async with session.begin():
        return await container.files.set_policy(session, principal, body)


@router.get('/policies')
async def policies(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[RetentionPolicyInfo]:
    return await container.files.retention_policies(session, principal)


@router.post('/policies', status_code=201)
async def create_policy(
    body: RetentionPolicyRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RetentionPolicyInfo:
    async with session.begin():
        return await container.files.set_retention(session, principal, body)


@router.patch('/policies/{resource_type}')
async def patch_policy(
    resource_type: Annotated[str, Path(min_length=1, max_length=32, pattern=r'^[a-z][a-z0-9_]*$')],
    body: RetentionPolicyPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RetentionPolicyInfo:
    async with session.begin():
        return await container.files.set_retention(
            session,
            principal,
            RetentionPolicyRequest(resource_type=resource_type, **body.model_dump(exclude={'expected_version'})),
            expected_version=body.expected_version,
        )


@router.delete('/policies/{resource_type}', status_code=204)
async def delete_policy(
    resource_type: Annotated[str, Path(min_length=1, max_length=32, pattern=r'^[a-z][a-z0-9_]*$')],
    body: VersionRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.files.set_retention(
            session,
            principal,
            RetentionPolicyRequest(resource_type=resource_type, retention_days=1),
            expected_version=body.expected_version,
            remove=True,
        )
    return Response(status_code=204)


@router.get('/jobs')
async def jobs(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[JobInfo]:
    return await container.files.jobs(session, principal)


@router.post('/jobs/{kind}')
async def run_job(
    kind: JobKind, container: ContainerDependency, principal: PrincipalDependency, _body: EmptyRequest
) -> JobInfo:
    return await container.file_jobs.run(principal, kind)


@router.get('/reminders')
async def reminders(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[ReminderInfo]:
    return await container.files.reminders(session, principal)


@router.post('/reminders/{reminder_id}/read')
async def read_reminder(
    reminder_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest,
) -> ReminderInfo:
    async with session.begin():
        return await container.files.read_reminder(session, principal, reminder_id)


@router.get('/reconcile/issues')
async def issues(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    status: Literal['open', 'resolved', 'ignored', 'quarantined'] = 'open',
) -> list[ReconcileIssueInfo]:
    return await container.files.issues(session, principal, status=status)


@router.get('/reconcile/stats')
async def issue_stats(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> dict[str, int]:
    return await container.files.issue_stats(session, principal)


@router.post('/reconcile/issues/{issue_id}/actions')
async def issue_action(
    issue_id: UUID, body: IssueAction, container: ContainerDependency, principal: PrincipalDependency
) -> ReconcileIssueInfo:
    return await container.file_jobs.issue_action(principal, issue_id, body)


@router.get('/{file_id}')
async def get(
    file_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> FileInfo:
    return await container.files.get(session, principal, file_id)


@router.get('/{file_id}/download')
async def download(
    file_id: UUID,
    request: Request,
    container: ContainerDependency,
    session: SessionDependency,
) -> Response:
    principal = None
    try:
        principal = await container.identity.authenticate(session, request.cookies.get(COOKIE_NAME))
        info, content = await container.files.read(session, principal, file_id)
    except AppError as error:
        async with container.database.sessions.begin() as audit_session:
            await container.files.record_access(
                audit_session,
                principal,
                file_id,
                outcome='denied',
                bytes_sent=0,
                range_header=request.headers.get('range'),
                error_code=error.code,
            )
        raise
    size = len(content)
    start, end = 0, size - 1
    status = 200
    headers = {
        'ETag': f'"{info.sha256}"',
        'Accept-Ranges': 'bytes',
        'X-Content-Type-Options': 'nosniff',
        'Content-Security-Policy': "default-src 'none'; sandbox",
        'Cache-Control': 'private, no-store',
        'Content-Disposition': f"attachment; filename=download; filename*=UTF-8''{quote(info.original_filename, safe='')}",
    }
    ranges = request.headers.getlist('range')
    if ranges:
        try:
            if len(ranges) != 1 or not ranges[0].startswith('bytes='):
                raise ValueError
            first, last = ranges[0][6:].split('-')
            if first:
                start = int(first)
                end = min(int(last), size - 1) if last else size - 1
            else:
                suffix = int(last)
                if suffix <= 0:
                    raise ValueError
                start = max(0, size - suffix)
            if start < 0 or end < start or start >= size:
                raise ValueError
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            status = 206
        except ValueError:
            return AuditedFileResponse(
                b'',
                status_code=416,
                headers={**headers, 'Content-Range': f'bytes */{size}'},
                container=container,
                principal=principal,
                file_id=file_id,
                range_header=request.headers.get('range'),
            )
    payload = content[start : end + 1]
    headers['Content-Length'] = str(len(payload))
    return AuditedFileResponse(
        payload,
        status_code=status,
        headers=headers,
        container=container,
        principal=principal,
        file_id=file_id,
        range_header=request.headers.get('range'),
    )


@router.patch('/{file_id}')
async def patch(
    file_id: UUID,
    body: FilePatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> FileInfo:
    async with session.begin():
        return await container.files.patch(session, principal, file_id, body)


@router.get('/{file_id}/acl')
async def acl(
    file_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> AclInfo:
    return await container.files.acl(session, principal, file_id)


@router.patch('/{file_id}/acl')
async def update_acl(
    file_id: UUID,
    body: AclRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> AclInfo:
    async with session.begin():
        return await container.files.set_acl(session, principal, file_id, body)


@router.post('/{file_id}/transfer')
async def transfer(
    file_id: UUID,
    body: TransferRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> FileInfo:
    async with session.begin():
        return await container.files.transfer(session, principal, file_id, body)


@router.delete('/{file_id}')
async def remove(
    file_id: UUID,
    body: VersionRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> FileInfo:
    async with session.begin():
        return await container.files.remove(session, principal, file_id, body.expected_version)


@router.post('/{file_id}/restore')
async def restore(
    file_id: UUID,
    body: VersionRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> FileInfo:
    async with session.begin():
        return await container.files.remove(session, principal, file_id, body.expected_version, restore=True)


@router.post('/{file_id}/purge')
async def purge(
    file_id: UUID, body: VersionRequest, container: ContainerDependency, principal: PrincipalDependency
) -> FileInfo:
    return await container.file_jobs.purge(principal, file_id, body.expected_version)


@router.post('/{file_id}/extend')
async def extend(
    file_id: UUID,
    body: ReminderAction,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> FileInfo:
    async with session.begin():
        return await container.files.extend(session, principal, file_id, body)


@router.post('/{file_id}/dispose-expired')
async def dispose(
    file_id: UUID,
    body: VersionRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> FileInfo:
    async with session.begin():
        return await container.files.dispose_expired(session, principal, file_id, body.expected_version)


@router.get('/{file_id}/references')
async def references(
    file_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[ReferenceInfo]:
    return await container.files.references(session, principal, file_id)


@router.get('/{file_id}/audit')
async def audits(
    file_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[AuditInfo]:
    return await container.files.audits(session, principal, file_id)


@router.post('/{file_id}/parse')
async def parse(
    file_id: UUID, container: ContainerDependency, principal: PrincipalDependency, _body: EmptyRequest
) -> ParsedFile:
    return await container.file_inputs.parse(principal, file_id)


@router.get('/{file_id}/audit/page')
async def audit_page(
    file_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    filters: Annotated[FileAuditQuery, Query()],
) -> FileAuditPage:
    return await container.files.audit_page(session, principal, file_id, filters)
