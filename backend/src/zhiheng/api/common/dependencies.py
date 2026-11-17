from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.api.common.platform_access import authorize_console_route
from zhiheng.bootstrap.container import Container
from zhiheng.shared.principal import Principal

COOKIE_NAME = 'zhiheng_session'
console_cookie = APIKeyCookie(name=COOKIE_NAME, scheme_name='ConsoleCookie', auto_error=False)


def get_container(request: Request) -> Container:
    return cast('Container', request.app.state.container)


ContainerDependency = Annotated[Container, Depends(get_container)]


async def get_ready_container(container: ContainerDependency) -> Container:
    # FastAPI caches this dependency once per request. Authentication still uses
    # its own short session; endpoint transactions keep their original ownership.
    await container.database.require_schema()
    return container


ReadyContainerDependency = Annotated[Container, Depends(get_ready_container)]


async def get_session(container: ReadyContainerDependency) -> AsyncIterator[AsyncSession]:
    async with container.database.sessions() as session:
        yield session


async def get_principal(
    request: Request,
    container: ReadyContainerDependency,
    _cookie: Annotated[str | None, Security(console_cookie)] = None,
) -> Principal:
    async with container.database.sessions() as session:
        principal = await container.identity.authenticate(session, request.cookies.get(COOKIE_NAME))
        authorize_console_route(request, principal)
        return principal


SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]
