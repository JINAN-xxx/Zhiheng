"""Cookie-only graph commands and persistent operation reads."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from zhiheng.api.contracts import EmptyRequest
from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.knowledge.public import (
    GraphBuildRequest,
    GraphConfigInfo,
    GraphConfigPatch,
    GraphJobInfo,
    GraphLabels,
    GraphPreviewRequest,
    GraphQueryRequest,
    GraphSubgraph,
    GraphSubgraphRequest,
)
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/knowledge/bases/{base_id}/graph', tags=['knowledge-graph'])


@router.get('/config')
async def config(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> GraphConfigInfo:
    available = await container.graph_sdk.available()
    async with session.begin():
        return await container.graph.config(session, principal, base_id, available=available)


@router.patch('/config')
async def patch_config(
    base_id: UUID,
    body: GraphConfigPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> GraphConfigInfo:
    available = await container.graph_sdk.available()
    async with session.begin():
        await container.graph.patch(session, principal, base_id, body)
        await session.flush()
        return await container.graph.config(session, principal, base_id, available=available)


@router.post('/build', status_code=202)
async def build(
    base_id: UUID,
    body: GraphBuildRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> GraphJobInfo:
    return await container.graph_jobs.submit(principal, base_id, body, 'build')


@router.post('/preview', status_code=202)
async def preview(
    base_id: UUID,
    body: GraphPreviewRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> GraphJobInfo:
    return await container.graph_jobs.submit(principal, base_id, body, 'preview')


@router.post('/query', status_code=202)
async def query(
    base_id: UUID,
    body: GraphQueryRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> GraphJobInfo:
    return await container.graph_jobs.submit(principal, base_id, body, 'query')


@router.get('/jobs')
async def jobs(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[GraphJobInfo]:
    async with session.begin():
        return await container.graph.jobs(session, principal, base_id)


@router.get('/jobs/{job_id}')
async def job(
    base_id: UUID,
    job_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> GraphJobInfo:
    async with session.begin():
        return await container.graph.job(session, principal, base_id, job_id)


@router.post('/jobs/{job_id}/cancel')
async def cancel(
    base_id: UUID,
    job_id: UUID,
    body: EmptyRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> GraphJobInfo:
    async with session.begin():
        return await container.graph.cancel(session, principal, base_id, job_id)


@router.get('/labels')
async def labels(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> GraphLabels:
    return await container.graph_jobs.labels(principal, base_id)


@router.get('/subgraph')
async def subgraph(
    base_id: UUID,
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    node_label: str = '*',
    max_depth: Annotated[int, Query(ge=1, le=5)] = 2,
    max_nodes: Annotated[int, Query(ge=1, le=200)] = 100,
) -> GraphSubgraph:
    if set(request.query_params) - {'node_label', 'max_depth', 'max_nodes'}:
        raise AppError('invalid_graph_parameters', '未知图谱查询参数', 422)
    return await container.graph_jobs.subgraph(
        principal, base_id, GraphSubgraphRequest(node_label=node_label, max_depth=max_depth, max_nodes=max_nodes)
    )
