import hashlib
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from zhiheng.api.common.contracts import EmptyRequest
from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.api.common.pagination import MAX_CURSOR_LENGTH, decode_cursor, encode_cursor
from zhiheng.modules.knowledge.public import (
    NATIVE_FORMATS,
    AttachmentImport,
    AttachmentImportInfo,
    BaseAclInfo,
    BaseCreate,
    BaseInfo,
    BasePatch,
    BuildSelection,
    DocumentImport,
    DocumentPage,
    FolderCreate,
    FolderInfo,
    FolderPatch,
    JobInfo,
    JobRequest,
    KnowledgeSettings,
    MigrationRequest,
    PreparationInfo,
    QueryRequest,
    RechunkRequest,
    UnavailableBaseInfo,
)
from zhiheng.shared.errors import AppError
from zhiheng.shared.text import storage_text

router = APIRouter(prefix='/api/knowledge', tags=['knowledge'])


@router.get('/capabilities')
async def capabilities(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> dict[str, object]:
    try:
        ranker = await container.knowledge.default_rerank_binding(session)
    except AppError:
        ranker = None
    return {
        'default_embedding_configured': await container.knowledge.defaults_ready(session),
        'default_rerank_configured': ranker is not None,
        'default_rerank_model_id': str(ranker.model_id) if ranker else None,
        'milvus_configured': bool(container.settings.milvus_uri),
        'milvus_available': await container.vectors.available(),
        'native_formats': NATIVE_FORMATS,
        'graph_available': await container.graph_sdk.available(),
        'mineru_available': await container.document_parsing.available(),
    }


@router.get('/bases')
async def bases(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    scope: Literal['owned', 'shared', 'all'] = 'all',
) -> list[BaseInfo | UnavailableBaseInfo]:
    return await container.knowledge.list_bases(session, principal, scope=scope)


@router.get('/available')
async def available(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    agent_id: UUID | None = None,
) -> list[BaseInfo]:
    if agent_id:
        agent = await container.agents.get(session, principal, agent_id)
        principal = await container.identity.principal_for_user(session, agent.owner_id)
    rows = await container.knowledge.list_bases(session, principal, available=True)
    return [row for row in rows if isinstance(row, BaseInfo)]


@router.post('/bases', status_code=201)
async def create(
    body: BaseCreate, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> BaseInfo:
    async with session.begin():
        return await container.knowledge_commands.native.create(session, principal, body)


@router.get('/bases/{base_id}')
async def get_base(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> BaseInfo:
    return await container.knowledge.detail(session, principal, base_id)


@router.patch('/bases/{base_id}')
async def patch_base(
    base_id: UUID,
    body: BasePatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> BaseInfo:
    async with session.begin():
        return await container.knowledge_commands.native.patch(session, principal, base_id, body)


@router.patch('/bases/{base_id}/settings')
async def configure(
    base_id: UUID,
    body: KnowledgeSettings,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> dict[str, object]:
    base, job = await container.knowledge_configuration.save(principal, base_id, body)
    return {'base': base.model_dump(mode='json'), 'job': job.model_dump(mode='json') if job else None}


@router.get('/bases/{base_id}/acl')
async def acl(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> BaseAclInfo:
    return await container.knowledge.acl(session, principal, base_id)


@router.get('/bases/{base_id}/folders')
async def folders(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[FolderInfo]:
    return await container.knowledge.folders(session, principal, base_id)


@router.post('/bases/{base_id}/folders', status_code=201)
async def create_folder(
    base_id: UUID,
    body: FolderCreate,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> FolderInfo:
    async with session.begin():
        return await container.knowledge_commands.native.folder_create(session, principal, base_id, body)


@router.patch('/bases/{base_id}/folders/{folder_id}')
async def rename_folder(
    base_id: UUID,
    folder_id: UUID,
    body: FolderPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> FolderInfo:
    async with session.begin():
        return await container.knowledge_commands.native.folder_patch(session, principal, base_id, folder_id, body)


@router.post('/bases/{base_id}/uploads', status_code=201, openapi_extra={'x-transport-policy': 'binary_file'})
async def upload(
    base_id: UUID,
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    folder_id: UUID | None = None,
    client_request_id: UUID | None = None,
) -> dict[str, object]:
    return await container.knowledge_documents.stage_upload(
        principal,
        base_id,
        filename=filename,
        content=await request.body(),
        folder_id=folder_id,
        client_request_id=client_request_id,
    )


@router.get('/bases/{base_id}/uploads/requests/{request_id}')
async def upload_status(
    base_id: UUID,
    request_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> dict[str, object]:
    return await container.knowledge_documents.upload_status(principal, base_id, request_id)


@router.get(
    '/bases/{base_id}/documents',
    responses={
        200: {
            'headers': {
                'X-Next-Cursor': {'description': '下一批资料的游标，空值表示末页', 'schema': {'type': 'string'}}
            }
        }
    },
)
async def documents(
    base_id: UUID,
    response: Response,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    folder: Annotated[UUID | Literal['root'] | None, Query()] = None,
    search: Annotated[str, Query(max_length=255)] = '',
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> DocumentPage:
    materials = container.knowledge.materials
    if folder is None:
        if cursor is not None or search:
            raise AppError('invalid_document_filter', '资料分页需要指定目录', 422)
        return await materials.documents(session, principal, base_id)
    search = search.strip()
    if storage_text(search) != search:
        raise AppError('invalid_document_filter', '搜索条件无效', 422)
    binding = hashlib.sha256(f'knowledge-documents:{principal.id}:{base_id}:{folder}:{search}'.encode()).hexdigest()
    page, next_position = await materials.document_page(
        session,
        principal,
        base_id,
        folder_id=None if folder == 'root' else folder,
        search=search,
        after=decode_cursor(cursor, binding),
        limit=limit,
    )
    response.headers['X-Next-Cursor'] = encode_cursor(binding, *next_position) if next_position else ''
    return page


@router.get('/bases/{base_id}/documents/visibility')
async def document_visibility(
    base_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    document_id: Annotated[list[UUID], Query(min_length=1, max_length=200)],
) -> list[UUID]:
    return await container.knowledge.materials.visible_document_ids(session, principal, base_id, document_id)


@router.delete('/bases/{base_id}/uploads/{upload_id}', status_code=204)
async def remove_upload(
    base_id: UUID,
    upload_id: UUID,
    body: EmptyRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.knowledge.materials.remove_upload(session, principal, base_id, upload_id)
    return Response(status_code=204)


@router.post('/bases/{base_id}/documents', status_code=202)
async def import_documents(
    base_id: UUID,
    body: DocumentImport,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JobInfo:
    return await container.knowledge_documents.import_files(principal, base_id, body)


@router.post('/bases/{base_id}/documents/{document_id}/rechunk', status_code=202)
async def rechunk(
    base_id: UUID,
    document_id: UUID,
    body: RechunkRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JobInfo:
    async with session.begin():
        return await container.knowledge_commands.native.console_job(
            session,
            principal,
            base_id,
            'rechunk',
            body.client_request_id,
            body.model_dump(mode='json'),
            BuildSelection(chunk_size=body.chunk_size, chunk_overlap=body.chunk_overlap),
            version=body.expected_version,
            child_id=document_id,
        )


@router.get('/jobs/{job_id}')
async def get_job(
    job_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> JobInfo:
    return await container.knowledge.builds.get_job_info(session, principal, job_id)


@router.post('/jobs/{job_id}/cancel')
async def cancel_job(
    job_id: UUID,
    body: EmptyRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JobInfo:
    async with session.begin():
        return await container.knowledge.builds.cancel_job(session, principal, job_id)


@router.get('/bases/{base_id}/documents/{document_id}/preview')
async def preview(
    base_id: UUID,
    document_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> dict[str, object]:
    return await container.knowledge_documents.preview(principal, base_id, document_id)


@router.post('/bases/{base_id}/query', status_code=202)
async def query(
    base_id: UUID,
    body: QueryRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> PreparationInfo:
    async with session.begin():
        return await container.knowledge_preparation.query(session, principal, base_id, body)


@router.get('/preparations/{intent_id}')
async def preparation(
    intent_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> PreparationInfo:
    return await container.knowledge_preparation.info(session, principal, intent_id)


@router.post('/preparations/{intent_id}/cancel')
async def cancel_preparation(
    intent_id: UUID,
    body: EmptyRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> PreparationInfo:
    async with session.begin():
        result = await container.knowledge_preparation.cancel(session, principal, intent_id)
    await container.knowledge_preparation.cancel_children_if_terminal(intent_id)
    return result


@router.get('/bases/{base_id}/jobs')
async def jobs(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[JobInfo]:
    return await container.knowledge.builds.jobs(session, principal, base_id)


@router.post('/bases/{base_id}/rebuild', status_code=202)
async def rebuild(
    base_id: UUID,
    body: JobRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JobInfo:
    async with session.begin():
        return await container.knowledge_commands.native.console_job(
            session,
            principal,
            base_id,
            'rebuild',
            body.client_request_id,
            body.model_dump(mode='json'),
            BuildSelection(),
            version=body.expected_version,
        )


@router.delete('/bases/{base_id}/documents/{document_id}', status_code=202)
async def delete_document(
    base_id: UUID,
    document_id: UUID,
    body: JobRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JobInfo:
    async with session.begin():
        return await container.knowledge_commands.native.console_job(
            session,
            principal,
            base_id,
            'delete_document',
            body.client_request_id,
            body.model_dump(mode='json'),
            BuildSelection(),
            version=body.expected_version,
            child_id=document_id,
        )


@router.delete('/bases/{base_id}/folders/{folder_id}', status_code=202)
async def delete_folder(
    base_id: UUID,
    folder_id: UUID,
    body: JobRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JobInfo:
    async with session.begin():
        return await container.knowledge_commands.native.console_job(
            session,
            principal,
            base_id,
            'delete_folder',
            body.client_request_id,
            body.model_dump(mode='json'),
            BuildSelection(),
            version=body.expected_version,
            child_id=folder_id,
        )


@router.delete('/bases/{base_id}', status_code=202)
async def delete_base(
    base_id: UUID,
    body: JobRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JobInfo:
    async with session.begin():
        return await container.knowledge_commands.native.console_job(
            session,
            principal,
            base_id,
            'delete_base',
            body.client_request_id,
            body.model_dump(mode='json'),
            BuildSelection(confirm_references=body.confirm_references),
            version=body.expected_version,
        )


@router.post('/bases/{base_id}/attachments', status_code=202)
async def add_attachment(
    base_id: UUID,
    body: AttachmentImport,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> AttachmentImportInfo:
    return await container.knowledge_documents.add_attachment(principal, base_id, body)


@router.get('/bases/{base_id}/documents/{document_id}/download')
async def download(
    base_id: UUID,
    document_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> Response:
    file, content = await container.knowledge_documents.download(principal, base_id, document_id)
    return Response(
        content,
        media_type=file.mime,
        headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(file.original_filename),
            'Cache-Control': 'no-store',
        },
    )


@router.get('/bases/{base_id}/references')
async def references(
    base_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict[str, object]:
    return await container.knowledge.references.for_base(session, principal, base_id)


@router.get('/bases/{base_id}/migration')
async def migration_preview(
    base_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict[str, object]:
    return await container.knowledge.migration.preview(session, principal, base_id)


@router.post('/bases/{base_id}/migration', status_code=202)
async def migration_confirm(
    base_id: UUID,
    body: MigrationRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> JobInfo:
    return await container.knowledge_documents.migrate(principal, base_id, body)


@router.get('/bases/{base_id}/uploads')
async def pending_uploads(
    base_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    folder_id: UUID | None = None,
) -> dict[str, object]:
    return await container.knowledge.materials.pending_uploads(session, principal, base_id, folder_id)
