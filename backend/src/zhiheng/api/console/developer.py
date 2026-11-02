from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from zhiheng.api.common.contracts import EmptyRequest
from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.application.developer_resources import ResourceAccessInfo, ResourceCandidate
from zhiheng.modules.developer.public import (
    ApplicationCreate,
    ApplicationInfo,
    ApplicationPatch,
    IssuedKey,
    KeyCreate,
    KeyInfo,
    KeyRotate,
    ResourceGrantInfo,
    ResourceGrantRemove,
    ResourceGrantRequest,
    ResourceKind,
    ResourceQuotaRequest,
)
from zhiheng.modules.developer.public import (
    scope_catalog as capabilities,
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


@router.get('/scope-catalog')
async def scope_catalog(_principal: PrincipalDependency) -> dict[str, object]:
    return capabilities()


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
        return await container.developer_management.transition(session, principal, application_id, 'delete')


@router.post('/{application_id}/enable')
async def enable(
    application_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> ApplicationInfo:
    async with session.begin():
        return await container.developer_management.transition(session, principal, application_id, 'enable')


@router.post('/{application_id}/disable')
async def disable(
    application_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> ApplicationInfo:
    async with session.begin():
        return await container.developer_management.transition(session, principal, application_id, 'disable')


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


@router.get('/{application_id}/resource-access')
async def resource_access_info(
    application_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ResourceAccessInfo:
    return await container.developer_resources.summary(session, principal, application_id)


@router.get('/{application_id}/resource-candidates')
async def resource_candidates(
    application_id: UUID,
    kind: ResourceKind,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> list[ResourceCandidate]:
    return await container.developer_resources.candidates(session, principal, application_id, kind)


@router.get('/{application_id}/resources')
async def resource_grants(
    application_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    kind: ResourceKind | None = None,
) -> list[ResourceGrantInfo]:
    return await container.resource_grants.list(session, principal.id, application_id, kind)


@router.put('/{application_id}/resources/{kind}/{resource_id}')
async def bind_resource(
    application_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    body: ResourceGrantRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ResourceGrantInfo:
    async with session.begin():
        return await container.resource_access.bind(
            session,
            principal,
            application_id,
            kind,
            resource_id,
            list(body.actions),
            expected_version=body.expected_version,
        )


@router.delete('/{application_id}/resources/{kind}/{resource_id}')
async def unbind_resource(
    application_id: UUID,
    kind: ResourceKind,
    resource_id: UUID,
    body: ResourceGrantRemove,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict[str, int]:
    async with session.begin():
        version = await container.resource_access.unbind(
            session, principal, application_id, kind, resource_id, expected_version=body.expected_version
        )
        return {'version': version}


@router.patch('/{application_id}/resource-quota')
async def resource_quota(
    application_id: UUID,
    body: ResourceQuotaRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict[str, int]:
    async with session.begin():
        current = await container.identity.principal_for_user(session, principal.id)
        version = await container.resource_grants.configure_quota(
            session,
            current,
            application_id,
            file_limit=body.source_file_limit,
            byte_limit=body.source_byte_limit,
            expected_version=body.expected_version,
        )
        return {'version': version}
