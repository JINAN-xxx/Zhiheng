"""Metadata for early rejections and resource requests that never reach metering."""

import logging
from http import HTTPStatus
from uuid import UUID, uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zhiheng.api.common.auth import bearer
from zhiheng.modules.developer.public import record_public_request
from zhiheng.shared.errors import AppError

logger = logging.getLogger(__name__)


class PublicRequestLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or not scope['path'].startswith(('/v1/', '/api/v1/')):
            await self.app(scope, receive, send)
            return
        state = scope.setdefault('state', {})
        request_id = str(state.get('resource_request_id') or uuid4())
        status = 0

        async def identified(message: Message) -> None:
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
                if not any(name.lower() == b'x-request-id' for name, _ in message['headers']):
                    message['headers'].append((b'x-request-id', request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, identified)
        finally:
            if status >= HTTPStatus.BAD_REQUEST or (status and state.get('public_resource_identity')):
                await self._record(scope, UUID(request_id), status)

    @staticmethod
    async def _record(scope: Scope, request_id: UUID, status: int) -> None:
        route = getattr(scope.get('route'), 'path', None)
        if not route:
            return
        state = scope['state']
        try:
            container = scope['app'].state.container
            async with container.database.sessions.begin() as session:
                identity = state.get('public_resource_identity')
                if identity is None:
                    identity = await container.keys.authenticate(session, bearer(Request(scope)))
                await record_public_request(
                    session, identity, request_id, scope['method'], route, status, state.get('public_error_code')
                )
        except AppError:
            return
        except Exception as error:
            logger.warning('Could not write public request diagnostics (%s)', type(error).__name__)
