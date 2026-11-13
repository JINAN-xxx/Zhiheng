from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.operations.public import (
    ConfigInfo,
    CreateConfig,
    CreateNotice,
    LogInfo,
    LogKind,
    LogOutcome,
    NoticeInfo,
    NoticeKind,
    NoticeStatus,
    Page,
    PatchConfig,
    PatchNotice,
)

router = APIRouter(prefix='/api/admin', tags=['admin-operations'])
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
Search = Annotated[str, Query(max_length=500)]
ExpectedVersion = Annotated[int, Query(ge=1)]


@router.get('/configs')
async def configs(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    search: Search = '',
    page: PageNumber = 1,
    page_size: PageSize = 20,
    is_active: bool | None = None,
) -> Page[ConfigInfo]:
    return await container.operations.list_configs(
        session, principal, search=search, page=page, page_size=page_size, is_active=is_active
    )


@router.post('/configs', status_code=201)
async def create_config(
    body: CreateConfig,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ConfigInfo:
    async with session.begin():
        return await container.operations.create_config(session, principal, body)


@router.get('/configs/{config_id}')
async def config(
    config_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ConfigInfo:
    return await container.operations.get_config(session, principal, config_id)


@router.patch('/configs/{config_id}')
async def update_config(
    config_id: UUID,
    body: PatchConfig,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ConfigInfo:
    async with session.begin():
        return await container.operations.update_config(session, principal, config_id, body)


@router.delete('/configs/{config_id}', status_code=204)
async def delete_config(
    config_id: UUID,
    expected_version: ExpectedVersion,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.operations.delete_config(session, principal, config_id, expected_version=expected_version)
    return Response(status_code=204)


@router.get('/notices/published')
async def published_notices(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> Page[NoticeInfo]:
    return await container.operations.published_notices(session, principal, page=page, page_size=page_size)


@router.get('/notices')
async def notices(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    search: Search = '',
    page: PageNumber = 1,
    page_size: PageSize = 20,
    status: NoticeStatus | None = None,
    kind: NoticeKind | None = None,
) -> Page[NoticeInfo]:
    return await container.operations.list_notices(
        session, principal, search=search, page=page, page_size=page_size, status=status, kind=kind
    )


@router.post('/notices', status_code=201)
async def create_notice(
    body: CreateNotice,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> NoticeInfo:
    async with session.begin():
        return await container.operations.create_notice(session, principal, body)


@router.get('/notices/{notice_id}')
async def notice(
    notice_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> NoticeInfo:
    return await container.operations.get_notice(session, principal, notice_id)


@router.patch('/notices/{notice_id}')
async def update_notice(
    notice_id: UUID,
    body: PatchNotice,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> NoticeInfo:
    async with session.begin():
        return await container.operations.update_notice(session, principal, notice_id, body)


@router.delete('/notices/{notice_id}', status_code=204)
async def delete_notice(
    notice_id: UUID,
    expected_version: ExpectedVersion,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.operations.delete_notice(session, principal, notice_id, expected_version=expected_version)
    return Response(status_code=204)


@router.get('/logs')
async def logs(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    search: Search = '',
    page: PageNumber = 1,
    page_size: PageSize = 20,
    kind: LogKind | None = None,
    outcome: LogOutcome | None = None,
) -> Page[LogInfo]:
    return await container.operations.list_logs(
        session, principal, search=search, page=page, page_size=page_size, kind=kind, outcome=outcome
    )
