"""Bearer-only dependencies for resource adapters; required capability is server metadata."""

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Annotated
from uuid import UUID

from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from zhiheng.api.common.auth import bearer
from zhiheng.api.common.dependencies import ContainerDependency, SessionDependency
from zhiheng.api.common.platform_access import route_identity
from zhiheng.application.access import CallerContext
from zhiheng.modules.developer.public import Scope
from zhiheng.shared.platform_permissions import require_capability

resource_bearer = HTTPBearer(scheme_name='ResourceBearer', auto_error=False)


def resource_caller(scope: Scope | None) -> Callable[..., Awaitable[CallerContext]]:
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
            if scope is None:
                identity = await container.keys.authenticate(authorization, token, lock=False)
                principal = await container.identity.principal_for_user(authorization, identity.owner_id)
                caller = CallerContext('key', principal, identity)
            else:
                caller = await container.resource_access.bearer(authorization, token, scope=scope, lock=False)
            module, name = route_identity(request)
            if module == 'knowledge':
                action = {
                    'create': 'create',
                    'patch': 'update',
                    'create_folder': 'create',
                    'patch_folder': 'update',
                    'import_documents': 'import',
                    'query': 'query',
                    'rebuild': 'rebuild',
                    'rechunk': 'update',
                    'remove_document': 'delete',
                    'remove_folder': 'delete',
                    'remove_base': 'delete',
                }.get(name, 'read')
                require_capability(caller.principal, 'knowledge', action)
            elif module == 'skills':
                if name in {'prepare', 'confirm'}:
                    caller.principal.require_any_permission('platform:skills:create', 'platform:skills:update')
                elif name == 'status':
                    caller.principal.require_any_permission('platform:skills:toggle', 'platform:skills:use')
                else:
                    require_capability(caller.principal, 'skills', 'delete' if name == 'remove' else 'read')
            request_id = request.scope.get('state', {}).get('resource_request_id')
            return replace(caller, request_id=UUID(request_id)) if request_id else caller

    return dependency
