"""Bearer-only dependencies for resource adapters; required capability is server metadata."""

from collections.abc import Awaitable, Callable

from fastapi import Request

from zhiheng.api.dependencies import ContainerDependency, SessionDependency
from zhiheng.api.routes.public import bearer
from zhiheng.application.access import CallerContext
from zhiheng.modules.developer.public import Scope


def resource_caller(scope: Scope) -> Callable[..., Awaitable[CallerContext]]:
    async def dependency(request: Request, container: ContainerDependency, session: SessionDependency) -> CallerContext:
        token = bearer(request)
        # Commit the resource bucket independently so a downstream 4xx/rollback still counts.
        async with container.database.sessions.begin() as admission:
            identity = await container.keys.authenticate(admission, token, lock=True)
            await container.resource_limits.consume(admission, identity)
        async with container.database.sessions() as authorization:
            return await container.resource_access.bearer(authorization, token, scope=scope, lock=False)

    return dependency
