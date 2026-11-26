"""MCP catalog and console management endpoints."""

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.mcp.public import (
    CallView,
    CatalogTool,
    ServerCreate,
    ServerUpdate,
    ServerVersion,
    ServerView,
    ToolGrant,
)

router = APIRouter(prefix='/api/mcp', tags=['mcp'])


class RoleOption(BaseModel):
    id: UUID
    name: str


class PublicationSource(BaseModel):
    server_id: UUID
    server_name: str
    version: int
    name: str
    read_only: bool


@router.get('/publication-sources')
async def publication_sources(
    container: ContainerDependency, session: SessionDependency, principal: PrincipalDependency
) -> list[PublicationSource]:
    return [
        PublicationSource(
            server_id=server.id,
            server_name=server.name,
            version=server.version,
            name=tool.name,
            read_only=tool.read_only,
        )
        for server in await container.mcp.servers(session, principal)
        if server.enabled
        for tool in server.tools
        if tool.can_publish
    ]


@router.get('/tools')
async def catalog(
    container: ContainerDependency, session: SessionDependency, principal: PrincipalDependency
) -> list[CatalogTool]:
    return await container.mcp.catalog(session, principal)


@router.get('/servers')
async def servers(
    container: ContainerDependency, session: SessionDependency, principal: PrincipalDependency
) -> list[ServerView]:
    return await container.mcp.servers(session, principal)


@router.get('/roles')
async def roles(
    container: ContainerDependency, session: SessionDependency, principal: PrincipalDependency
) -> list[RoleOption]:
    values = await container.identity.active_role_options(session)
    return [RoleOption(id=identifier, name=name) for identifier, name in values.items()]


@router.post('/servers', status_code=201)
async def create(
    body: ServerCreate, container: ContainerDependency, session: SessionDependency, principal: PrincipalDependency
) -> ServerView:
    async with session.begin():
        return await container.mcp.create(session, principal, body)


@router.patch('/servers/{server_id}')
async def update(
    server_id: UUID,
    body: ServerUpdate,
    container: ContainerDependency,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServerView:
    async with session.begin():
        return await container.mcp.update(session, principal, server_id, body)


@router.post('/servers/{server_id}/sync')
async def sync(
    server_id: UUID, body: ServerVersion, container: ContainerDependency, principal: PrincipalDependency
) -> ServerView:
    return await container.mcp.sync(principal, server_id, body.expected_version)


@router.put('/servers/{server_id}/tools/{name}')
async def grant(
    server_id: UUID,
    name: str,
    body: ToolGrant,
    container: ContainerDependency,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ServerView:
    async with session.begin():
        return await container.mcp.grant(session, principal, server_id, name, body)


@router.get('/servers/{server_id}/calls')
async def calls(
    server_id: UUID, container: ContainerDependency, session: SessionDependency, principal: PrincipalDependency
) -> list[CallView]:
    return await container.mcp.calls(session, server_id)
