"""Resource HTTP identity and error shape, shared with early transport rejections."""

from collections.abc import Mapping
from http import HTTPStatus
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


def is_resource(scope: Scope) -> bool:
    return bool(scope.get('path', '').startswith('/api/v1/'))


def resource_error(
    request: Request,
    code: str,
    message: str,
    status: int,
    details: dict[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = str(request.scope.setdefault('state', {}).setdefault('resource_request_id', str(uuid4())))
    response_headers = {'X-Request-ID': request_id, **(headers or {})}
    if status == HTTPStatus.UNAUTHORIZED:
        response_headers['WWW-Authenticate'] = 'Bearer'
    if status == HTTPStatus.TOO_MANY_REQUESTS:
        retry_after = details.get('retry_after') if details is not None else None
        response_headers.setdefault('Retry-After', str(retry_after) if retry_after is not None else '60')
    return JSONResponse(
        {'error': {'code': code, 'message': message, 'details': details, 'request_id': request_id}},
        status_code=status,
        headers=response_headers,
    )


class ResourceProtocolMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or not is_resource(scope):
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault('state', {})['resource_request_id'] = request_id

        async def identified(message: Message) -> None:
            if message['type'] == 'http.response.start':
                message['headers'] = [(k, v) for k, v in message['headers'] if k.lower() != b'x-request-id']
                message['headers'].append((b'x-request-id', request_id.encode()))
            await send(message)

        await self.app(scope, receive, identified)
