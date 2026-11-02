from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from zhiheng.api.contracts import EmptyRequest
from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.knowledge.public import (
    NATIVE_FORMATS,
    BaseAcl,
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
    PreparationInfo,
    QueryRequest,
    RechunkRequest,
    split_chunks,
)
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/knowledge', tags=['knowledge'])


@router.get('/capabilities')
async def capabilities(container: ContainerDependency, principal: PrincipalDependency) -> dict[str, object]:
    return {
        'milvus_configured': bool(container.settings.milvus_uri),
        'milvus_available': await container.vectors.available(),
        'native_formats': NATIVE_FORMATS,
        'graph_available': await container.graph_sdk.available(),
        'mineru_available': False,
    }


@router.get('/bases')
async def bases(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[BaseInfo]:
    return await container.knowledge.list_bases(session, principal)


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
    return await container.knowledge.list_bases(session, principal, available=True)


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


@router.get('/bases/{base_id}/acl')
async def acl(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> BaseAclInfo:
    return await container.knowledge.acl(session, principal, base_id)


@router.patch('/bases/{base_id}/acl')
async def set_acl(
    base_id: UUID,
    body: BaseAcl,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> BaseAclInfo:
    async with session.begin():
        return await container.knowledge.set_acl(session, principal, base_id, body)


@router.get('/bases/{base_id}/users')
async def users(
    base_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    search: Annotated[str, Query(max_length=80)] = '',
) -> list[dict[str, object]]:
    return [row.model_dump() for row in await container.knowledge.users(session, principal, base_id, search)]


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
    session: SessionDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    folder_id: UUID | None = None,
) -> dict[str, object]:
    async with session.begin():
        base = await container.knowledge.authorize_base(session, principal, base_id, manage=True)
        container.knowledge.idle(base)
        owner_id = base.owner_id
        await container.knowledge.authorize_folder(session, base_id, folder_id)
    content = await request.body()
    key = await container.files.storage.put(content)
    try:
        async with session.begin():
            file = await container.files.create_owned(
                session, principal, owner_id=owner_id, filename=filename, content=content, storage_key=key
            )
            upload_id = await container.knowledge.register_upload(session, principal, base_id, file, folder_id)
        return {'file': file.model_dump(mode='json'), 'upload_id': upload_id, 'folder_id': folder_id}
    except BaseException:
        await container.files.storage.delete(key)
        raise


@router.get('/bases/{base_id}/documents')
async def documents(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> DocumentPage:
    return await container.knowledge.documents(session, principal, base_id)


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
        await container.knowledge.remove_upload(session, principal, base_id, upload_id)
    return Response(status_code=204)


@router.post('/bases/{base_id}/documents', status_code=202)
async def import_documents(
    base_id: UUID,
    body: DocumentImport,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JobInfo:
    async with session.begin():
        return await container.knowledge_commands.native.console_job(
            session,
            principal,
            base_id,
            'import',
            body.client_request_id,
            body.model_dump(mode='json'),
            BuildSelection(
                file_ids=body.file_ids,
                folder_id=body.folder_id,
                chunk_size=body.chunk_size,
                chunk_overlap=body.chunk_overlap,
            ),
        )


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
    return await container.knowledge.get_job_info(session, principal, job_id)


@router.post('/jobs/{job_id}/cancel')
async def cancel_job(
    job_id: UUID,
    body: EmptyRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> JobInfo:
    async with session.begin():
        return await container.knowledge.cancel_job(session, principal, job_id)


@router.get('/bases/{base_id}/documents/{document_id}/preview')
async def preview(
    base_id: UUID,
    document_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict[str, object]:
    async with session.begin():
        await container.knowledge.authorize_base(session, principal, base_id)
        document = await container.knowledge.document_info(session, base_id, document_id)
        await container.files.require(session, principal, document.file_id, active=True)
        file_id, size, overlap = document.file_id, document.chunk_size, document.chunk_overlap
    parsed = await container.file_inputs.parse(principal, file_id)
    if str(parsed.metadata.get('format', '')).lower() not in NATIVE_FORMATS:
        raise AppError('ocr_required', '图像知识预览需要OCR解析', 422)
    try:
        count = len(split_chunks(parsed.text, size, overlap))
    except ValueError:
        raise AppError('invalid_chunks', '知识分块无效或超过2000块', 422) from None
    async with session.begin():
        await container.knowledge.authorize_base(session, principal, base_id)
        await container.knowledge.document_info(session, base_id, document_id)
        await container.files.require(session, principal, file_id, active=True)
    return {
        'document_id': document_id,
        'file_id': file_id,
        'text': parsed.text,
        'parser_version': parsed.parser_version,
        'chunk_count': count,
    }


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
    return await container.knowledge.jobs(session, principal, base_id)


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
            BuildSelection(),
            version=body.expected_version,
        )
