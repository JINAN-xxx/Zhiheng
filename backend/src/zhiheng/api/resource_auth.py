"""Bearer-only dependencies for resource adapters; required capability is server metadata."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Annotated
from uuid import UUID

from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from zhiheng.api.dependencies import ContainerDependency, SessionDependency
from zhiheng.api.routes.public import bearer
from zhiheng.application.access import CallerContext
from zhiheng.modules.developer.public import Scope

resource_bearer = HTTPBearer(scheme_name='ResourceBearer', auto_error=False)


def resource_caller(scope: Scope) -> Callable[..., Awaitable[CallerContext]]:
    async def dependency(
        request: Request,
        container: ContainerDependency,
        session: SessionDependency,
        _security: Annotated[HTTPAuthorizationCredentials | None, Security(resource_bearer)] = None,
    ) -> CallerContext:
        token = bearer(request)
        # Commit the resource bucket independently so a downstream 4xx/rollback still counts.
        async with container.database.sessions.begin() as admission:
            identity = await container.keys.authenticate(admission, token, lock=True)
            await container.resource_limits.consume(admission, identity)
        async with container.database.sessions() as authorization:
            caller = await container.resource_access.bearer(authorization, token, scope=scope, lock=False)
            request_id = request.scope.get('state', {}).get('resource_request_id')
            return replace(caller, request_id=UUID(request_id)) if request_id else caller

    return dependency
