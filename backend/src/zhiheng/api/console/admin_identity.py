"""Identity administration HTTP endpoints. Every write and audit share one transaction."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.api.common.dependencies import COOKIE_NAME, ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.identity.public import AdminKind, PasswordReset, permission_catalog
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/admin', tags=['admin-identity'])


@asynccontextmanager
async def _transaction(session: AsyncSession) -> AsyncIterator[None]:
    try:
        async with session.begin():
            yield
    except IntegrityError as exc:
        raise AppError('admin_write_conflict', '记录已存在或关联状态已变化，请刷新后重试', 409) from exc


@router.get('/permissions')
async def permissions(principal: PrincipalDependency) -> dict[str, Any]:
    if not principal.is_admin and not any(code.startswith('system:') for code in principal.permissions):
        raise AppError('forbidden', '需要后台管理权限', 403)
    return {'items': permission_catalog()}


@router.get('/users/options')
async def user_options(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict[str, Any]:
    return await container.admin_identity.user_options(session, principal)


@router.post('/users/{identifier}/password')
async def reset_password(
    identifier: UUID,
    body: PasswordReset,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> dict[str, Any]:
    async with _transaction(session):
        result = await container.admin_identity.reset_password(
            session,
            principal,
            identifier,
            body.password.get_secret_value(),
            expected_version=body.expected_version,
        )
        await container.operations.record(
            session,
            principal=principal,
            action='users.reset_password',
            resource_type='users',
            resource_id=str(identifier),
        )
        return result


@router.get('/sessions')
async def sessions(
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    search: Annotated[str, Query(max_length=200)] = '',
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> dict[str, Any]:
    return await container.admin_identity.list_sessions(
        session,
        principal,
        current_token=request.cookies.get(COOKIE_NAME),
        search=search,
        page=page,
        page_size=page_size,
    )


@router.delete('/sessions/{identifier}', status_code=204)
async def revoke_session(
    identifier: UUID,
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    expected_version: Annotated[int, Query(ge=1)],
) -> Response:
    async with _transaction(session):
        await container.admin_identity.revoke_session(
            session,
            principal,
            identifier,
            current_token=request.cookies.get(COOKIE_NAME),
            expected_version=expected_version,
        )
        await container.operations.record(
            session,
            principal=principal,
            action='sessions.revoke',
            resource_type='sessions',
            resource_id=str(identifier),
        )
    return Response(status_code=204)


def _resource_router(kind: AdminKind) -> APIRouter:
    resource = APIRouter(prefix=f'/{kind}')

    @resource.get('')
    async def list_records(
        container: ContainerDependency,
        principal: PrincipalDependency,
        session: SessionDependency,
        search: Annotated[str, Query(max_length=200)] = '',
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=200)] = 20,
        is_active: bool | None = None,
        navigation: bool = False,
    ) -> dict[str, Any]:
        if navigation:
            if kind != 'menus':
                raise AppError('invalid_navigation', '仅菜单资源支持导航投影', 422)
            return await container.admin_identity.navigation(session, principal)
        return await container.admin_identity.list(
            session,
            principal,
            kind,
            search=search,
            page=page,
            page_size=page_size,
            is_active=is_active,
        )

    @resource.get('/{identifier}')
    async def get_record(
        identifier: UUID,
        container: ContainerDependency,
        principal: PrincipalDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        return await container.admin_identity.get(session, principal, kind, identifier)

    @resource.post('', status_code=201)
    async def create_record(
        body: dict[str, Any],
        container: ContainerDependency,
        principal: PrincipalDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        async with _transaction(session):
            result = await container.accounts.create(session, principal, kind, body)
            await container.operations.record(
                session,
                principal=principal,
                action=f'{kind}.create',
                resource_type=kind,
                resource_id=str(result['id']),
            )
            return result

    @resource.patch('/{identifier}')
    async def update_record(
        identifier: UUID,
        body: dict[str, Any],
        container: ContainerDependency,
        principal: PrincipalDependency,
        session: SessionDependency,
    ) -> dict[str, Any]:
        fields = dict(body)
        expected_version = fields.pop('expected_version', None)
        if not isinstance(expected_version, int) or isinstance(expected_version, bool) or expected_version < 1:
            raise AppError('expected_version_required', '更新需要有效的 expected_version', 422)
        async with _transaction(session):
            result = await container.admin_identity.update(
                session,
                principal,
                kind,
                identifier,
                fields,
                expected_version=expected_version,
            )
            await container.operations.record(
                session,
                principal=principal,
                action=f'{kind}.update',
                resource_type=kind,
                resource_id=str(identifier),
            )
            return result

    @resource.delete('/{identifier}', status_code=204)
    async def delete_record(
        identifier: UUID,
        container: ContainerDependency,
        principal: PrincipalDependency,
        session: SessionDependency,
        expected_version: Annotated[int, Query(ge=1)],
    ) -> Response:
        async with _transaction(session):
            await container.admin_identity.delete(
                session, principal, kind, identifier, expected_version=expected_version
            )
            await container.operations.record(
                session,
                principal=principal,
                action=f'{kind}.delete',
                resource_type=kind,
                resource_id=str(identifier),
            )
        return Response(status_code=204)

    return resource


for _kind in ('users', 'roles', 'departments', 'posts', 'menus'):
    router.include_router(_resource_router(_kind))
