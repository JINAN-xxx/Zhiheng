from collections.abc import AsyncIterator, Awaitable
from typing import Annotated, Literal

from fastapi import File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.router import APIRouterPro
from module_admin.entity.vo.user_vo import CurrentUserModel
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.knowledge.models import (
    ChunkParameters,
    DatabaseCreate,
    DatabaseUpdate,
    DocumentAdd,
    FolderCreate,
    FolderUpdate,
    GraphBuildRequest,
    GraphConfiguration,
    GraphPreviewRequest,
    KnowledgeError,
    KnowledgeQuery,
)
from plugins.ai.knowledge.runtime import KnowledgeRuntime
from utils.response_util import ResponseUtil

ai_knowledge_controller = APIRouterPro(
    prefix='/ai/knowledge',
    order_num=24,
    tags=['知识库'],
    dependencies=[PreAuthDependency()],
)


def _runtime(request: Request) -> KnowledgeRuntime:
    runtime = getattr(request.app.state, 'knowledge_runtime', None)
    if runtime is None:
        error = KnowledgeError.unavailable()
        raise HTTPException(status_code=error.status_code, detail=error.public_dict())
    return runtime


async def _call(operation: Awaitable) -> Response:
    try:
        return ResponseUtil.success(data=await operation)
    except KnowledgeError as error:
        raise HTTPException(status_code=error.status_code, detail=error.public_dict()) from None
    except Exception:
        error = KnowledgeError.unavailable()
        raise HTTPException(status_code=error.status_code, detail=error.public_dict()) from None


def _owner(current_user: CurrentUserModel) -> int:
    if current_user.user is None or current_user.user.user_id is None:
        raise HTTPException(status_code=401, detail='当前用户信息无效')
    return current_user.user.user_id


@ai_knowledge_controller.get('/available', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')])
async def available_databases(request: Request) -> Response:
    return await _call(_runtime(request).list_databases())


@ai_knowledge_controller.get('/databases', dependencies=[UserInterfaceAuthDependency('ai:knowledge:list')])
async def list_databases(request: Request) -> Response:
    return await _call(_runtime(request).list_databases())


@ai_knowledge_controller.post('/databases', dependencies=[UserInterfaceAuthDependency('ai:knowledge:add')])
async def create_database(request: Request, payload: DatabaseCreate) -> Response:
    return await _call(_runtime(request).create_database(payload))


@ai_knowledge_controller.get('/types', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')])
async def list_types(request: Request) -> Response:
    return await _call(_runtime(request).types())


@ai_knowledge_controller.get('/models', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')])
async def list_models(
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    model_type: Literal['embedding', 'chat'] = Query('embedding'),
) -> Response:
    rows = (
        (
            await query_db.execute(
                select(AiModelCatalog.model_id, AiModelCatalog.display_name, AiModelCatalog.model_code)
                .join(
                    AiResourceBinding,
                    (AiResourceBinding.resource_id == AiModelCatalog.model_id)
                    & (AiResourceBinding.resource_type == 'model'),
                )
                .where(
                    AiModelCatalog.model_type == model_type,
                    AiModelCatalog.lifecycle_status == 'active',
                    AiResourceBinding.binding_status == 'active',
                )
                .order_by(AiModelCatalog.display_name)
            )
        )
        .mappings()
        .all()
    )
    return ResponseUtil.success(data={'models': [dict(row) for row in rows]})


@ai_knowledge_controller.get('/databases/{db_id}', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')])
async def get_database(request: Request, db_id: str) -> Response:
    return await _call(_runtime(request).get_database(db_id))


@ai_knowledge_controller.get(
    '/databases/{db_id}/graph-build', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')]
)
async def graph_build_status(request: Request, db_id: str) -> Response:
    return await _call(_runtime(request).graph_build_status(db_id))


@ai_knowledge_controller.post(
    '/databases/{db_id}/graph-build', dependencies=[UserInterfaceAuthDependency('ai:knowledge:edit')]
)
async def start_graph_build(request: Request, db_id: str, payload: GraphBuildRequest) -> Response:
    return await _call(_runtime(request).start_graph_build(db_id, payload))


@ai_knowledge_controller.get(
    '/databases/{db_id}/graph-config', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')]
)
async def graph_config_status(request: Request, db_id: str) -> Response:
    return await _call(_runtime(request).graph_config_status(db_id))


@ai_knowledge_controller.put(
    '/databases/{db_id}/graph-config', dependencies=[UserInterfaceAuthDependency('ai:knowledge:edit')]
)
async def save_graph_config(request: Request, db_id: str, payload: GraphConfiguration) -> Response:
    return await _call(_runtime(request).save_graph_config(db_id, payload))


@ai_knowledge_controller.get(
    '/databases/{db_id}/graph-preview', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')]
)
async def graph_preview_status(request: Request, db_id: str) -> Response:
    return await _call(_runtime(request).graph_preview_status(db_id))


@ai_knowledge_controller.post(
    '/databases/{db_id}/graph-preview', dependencies=[UserInterfaceAuthDependency('ai:knowledge:edit')]
)
async def start_graph_preview(request: Request, db_id: str, payload: GraphPreviewRequest) -> Response:
    return await _call(_runtime(request).start_graph_preview(db_id, payload))


@ai_knowledge_controller.put('/databases/{db_id}', dependencies=[UserInterfaceAuthDependency('ai:knowledge:edit')])
async def update_database(request: Request, db_id: str, payload: DatabaseUpdate) -> Response:
    return await _call(_runtime(request).update_database(db_id, payload.name, payload.description))


@ai_knowledge_controller.delete('/databases/{db_id}', dependencies=[UserInterfaceAuthDependency('ai:knowledge:remove')])
async def delete_database(request: Request, db_id: str) -> Response:
    return await _call(_runtime(request).delete_database(db_id))


@ai_knowledge_controller.get('/folders', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')])
async def list_folders(request: Request, db_id: str = Query(...)) -> Response:
    return await _call(_runtime(request).list_folders(db_id))


@ai_knowledge_controller.post('/folders', dependencies=[UserInterfaceAuthDependency('ai:knowledge:edit')])
async def create_folder(request: Request, payload: FolderCreate) -> Response:
    return await _call(_runtime(request).create_folder(payload.db_id, payload.name, payload.parent_id))


@ai_knowledge_controller.put('/folders/{folder_id}', dependencies=[UserInterfaceAuthDependency('ai:knowledge:edit')])
async def rename_folder(request: Request, folder_id: str, payload: FolderUpdate, db_id: str = Query(...)) -> Response:
    return await _call(_runtime(request).rename_folder(db_id, folder_id, payload.name))


@ai_knowledge_controller.delete(
    '/folders/{folder_id}', dependencies=[UserInterfaceAuthDependency('ai:knowledge:remove')]
)
async def delete_folder(request: Request, folder_id: str, db_id: str = Query(...)) -> Response:
    return await _call(_runtime(request).delete_folder(db_id, folder_id))


@ai_knowledge_controller.post('/files/upload', dependencies=[UserInterfaceAuthDependency('ai:knowledge:edit')])
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    db_id: str = Query(...),
    folder_id: str | None = Query(None),
) -> Response:
    async def chunks() -> AsyncIterator[bytes]:
        while chunk := await file.read(64 * 1024):
            yield chunk

    try:
        return await _call(_runtime(request).upload(db_id, file.filename or '', chunks(), folder_id))
    finally:
        await file.close()


@ai_knowledge_controller.get(
    '/databases/{db_id}/documents', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')]
)
async def list_documents(request: Request, db_id: str) -> Response:
    return await _call(_runtime(request).list_documents(db_id))


@ai_knowledge_controller.post(
    '/databases/{db_id}/documents', dependencies=[UserInterfaceAuthDependency('ai:knowledge:edit')]
)
async def add_documents(request: Request, db_id: str, payload: DocumentAdd) -> Response:
    return await _call(_runtime(request).add_documents(db_id, payload.items, payload.params))


@ai_knowledge_controller.get(
    '/databases/{db_id}/documents/{doc_id}/preview', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')]
)
async def preview_document(request: Request, db_id: str, doc_id: str) -> Response:
    return await _call(_runtime(request).preview(db_id, doc_id))


@ai_knowledge_controller.delete(
    '/databases/{db_id}/documents/{doc_id}', dependencies=[UserInterfaceAuthDependency('ai:knowledge:remove')]
)
async def delete_document(request: Request, db_id: str, doc_id: str) -> Response:
    return await _call(_runtime(request).delete_document(db_id, doc_id))


@ai_knowledge_controller.post(
    '/databases/{db_id}/documents/{doc_id}/rechunk', dependencies=[UserInterfaceAuthDependency('ai:knowledge:edit')]
)
async def rechunk_document(request: Request, db_id: str, doc_id: str, payload: ChunkParameters) -> Response:
    return await _call(_runtime(request).rechunk(db_id, doc_id, payload))


@ai_knowledge_controller.post(
    '/databases/{db_id}/query', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')]
)
async def query_database(
    request: Request,
    db_id: str,
    payload: KnowledgeQuery,
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    return await _call(_runtime(request).query(_owner(current_user), db_id, payload.query, payload.meta))


@ai_knowledge_controller.post(
    '/databases/{db_id}/query-graph', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')]
)
async def query_graph(request: Request, db_id: str, payload: KnowledgeQuery) -> Response:
    return await _call(_runtime(request).graph_query(db_id, payload.query, payload.meta))


@ai_knowledge_controller.get('/graphs/{db_id}/labels', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')])
async def graph_labels(request: Request, db_id: str) -> Response:
    return await _call(_runtime(request).graph_labels(db_id))


@ai_knowledge_controller.get(
    '/graphs/{db_id}/subgraph', dependencies=[UserInterfaceAuthDependency('ai:knowledge:query')]
)
async def graph_subgraph(
    request: Request,
    db_id: str,
    node_label: str = Query('*', max_length=500),
    max_depth: int = Query(2, ge=1, le=5),
    max_nodes: int = Query(100, ge=1, le=500),
) -> Response:
    return await _call(_runtime(request).graph_subgraph(db_id, node_label, max_depth, max_nodes))
