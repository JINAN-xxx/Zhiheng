from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.bootstrap.container import Container
from zhiheng.shared.principal import Principal

COOKIE_NAME = 'zhiheng_session'


def get_container(request: Request) -> Container:
    return cast('Container', request.app.state.container)


ContainerDependency = Annotated[Container, Depends(get_container)]


async def get_session(container: ContainerDependency) -> AsyncIterator[AsyncSession]:
    await container.database.require_schema()
    async with container.database.sessions() as session:
        yield session


async def get_principal(request: Request, container: ContainerDependency) -> Principal:
    await container.database.require_schema()
    async with container.database.sessions() as session:
        return await container.identity.authenticate(session, request.cookies.get(COOKIE_NAME))


SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[Principal, Depends(get_principal)]
