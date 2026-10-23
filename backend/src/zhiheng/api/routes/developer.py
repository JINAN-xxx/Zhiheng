from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from zhiheng.api.contracts import EmptyRequest
from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.developer.public import (
    ApplicationCreate,
    ApplicationInfo,
    ApplicationPatch,
    IssuedKey,
    KeyCreate,
    KeyInfo,
    KeyRotate,
)

router = APIRouter(prefix='/api/developer/applications', tags=['developer'])
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get('')
async def applications(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: Page = 1,
    page_size: PageSize = 20,
    search: str | None = None,
) -> dict[str, object]:
    return await container.applications.list(session, principal, page, page_size, search)


@router.post('', status_code=201)
async def create(
    body: ApplicationCreate, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ApplicationInfo:
    async with session.begin():
        return await container.applications.create(session, principal, body)


@router.get('/{application_id}')
async def application(
    application_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ApplicationInfo:
    return container.applications.info(await container.applications.require(session, principal, application_id))


@router.patch('/{application_id}')
async def update(
    application_id: UUID,
    body: ApplicationPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ApplicationInfo:
    async with session.begin():
        return await container.applications.update(session, principal, application_id, body)


@router.delete('/{application_id}')
async def delete(
    application_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ApplicationInfo:
    async with session.begin():
        return await container.applications.transition(session, principal, application_id, 'delete')


@router.post('/{application_id}/enable')
async def enable(
    application_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> ApplicationInfo:
    async with session.begin():
        return await container.applications.transition(session, principal, application_id, 'enable')


@router.post('/{application_id}/disable')
async def disable(
    application_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> ApplicationInfo:
    async with session.begin():
        return await container.applications.transition(session, principal, application_id, 'disable')


@router.get('/{application_id}/keys')
async def keys(
    application_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: Page = 1,
    page_size: PageSize = 20,
) -> dict[str, object]:
    return await container.keys.list(session, principal, application_id, page, page_size)


@router.post('/{application_id}/keys', status_code=201)
async def issue(
    application_id: UUID,
    body: KeyCreate,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> IssuedKey:
    async with session.begin():
        return await container.keys.create(session, principal, application_id, body)


@router.get('/{application_id}/keys/{key_id}')
async def key(
    application_id: UUID,
    key_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> KeyInfo:
    return container.keys.info(await container.keys.require(session, principal, application_id, key_id))


@router.post('/{application_id}/keys/{key_id}/rotate', status_code=201)
async def rotate(
    application_id: UUID,
    key_id: UUID,
    body: KeyRotate,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> IssuedKey:
    async with session.begin():
        return await container.keys.rotate(session, principal, application_id, key_id, body.grace_period_seconds)


@router.post('/{application_id}/keys/{key_id}/revoke')
async def revoke(
    application_id: UUID,
    key_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> KeyInfo:
    async with session.begin():
        return await container.keys.revoke(session, principal, application_id, key_id)


@router.get('/{application_id}/usage')
async def usage(
    application_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: Page = 1,
    page_size: PageSize = 20,
    operation: Literal['chat', 'embedding', 'rerank'] | None = None,
    status: Literal['pending', 'known', 'unknown'] | None = None,
) -> dict[str, object]:
    await container.applications.require(session, principal, application_id)
    return await container.usage.application_usage(session, application_id, page, page_size, operation, status)
