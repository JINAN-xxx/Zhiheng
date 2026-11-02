from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zhiheng.api.common.resource_protocol import is_resource, resource_error
from zhiheng.api.common.transport import TransportPolicy, transport_policy

MAX_LENGTH_HEADER_DIGITS = 20


class RequestOriginMiddleware:
    """Validate same-origin writes and bound the single binary upload route."""

    def __init__(self, app: ASGIApp, allowed_origins: tuple[str, ...]) -> None:
        self._app = app
        self._allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope['type'] == 'http'
            and scope['path'].startswith('/api/')
            and scope['method'] not in {'GET', 'HEAD', 'OPTIONS'}
        ):
            request = Request(scope)
            origin = request.headers.get('origin')
            allowed = {str(request.base_url).rstrip('/'), *self._allowed_origins}
            if not is_resource(scope) and origin is not None and origin.rstrip('/') not in allowed:
                response = JSONResponse(
                    {'error': {'code': 'forbidden_origin', 'message': '请求来源不受信任'}}, status_code=403
                )
                await response(scope, receive, send)
                return
            policy = transport_policy(scope)
            content_types = request.headers.getlist('content-type')
            content_type = content_types[0].split(';', 1)[0].strip().lower() if len(content_types) == 1 else ''
            if policy.media_type is not None and content_type != policy.media_type:
                await self._reject(scope, request, receive, send, 415, 'invalid_content_type', '请求内容类型无效')
                return
            await self._bounded(scope, request, receive, send, policy)
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _reject(
        scope: Scope, request: Request, receive: Receive, send: Send, status: int, code: str, message: str
    ) -> None:
        response = (
            resource_error(request, code, message, status)
            if is_resource(scope)
            else JSONResponse({'error': {'code': code, 'message': message}}, status_code=status)
        )
        await response(scope, receive, send)

    async def _bounded(
        self, scope: Scope, request: Request, receive: Receive, send: Send, policy: TransportPolicy
    ) -> None:
        lengths = request.headers.getlist('content-length')
        if len(lengths) > 1 or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit())):
            await self._reject(scope, request, receive, send, 400, 'invalid_content_length', '无效请求长度')
            return
        if lengths and (len(lengths[0]) > MAX_LENGTH_HEADER_DIGITS or int(lengths[0]) > policy.max_bytes):
            await self._reject(scope, request, receive, send, 413, 'request_too_large', '请求内容超过大小限制')
            return
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > policy.max_bytes:
                await self._reject(scope, request, receive, send, 413, 'request_too_large', '请求内容超过大小限制')
                return
            if not message.get('more_body', False):
                break
        if lengths and int(lengths[0]) != len(body):
            await self._reject(scope, request, receive, send, 400, 'invalid_content_length', '请求长度不匹配')
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self._app(scope, replay, send)


class PublicTransportMiddleware:
    """Bound incoming bodies before validation or durable admission."""

    def __init__(self, app: ASGIApp, max_body_bytes: int = 1048576) -> None:
        self._app, self._max = app, max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or not scope['path'].startswith('/v1/'):
            await self._app(scope, receive, send)
            return
        request = Request(scope)
        if scope['method'] != 'POST':
            await self._app(scope, receive, send)
            return
        lengths = request.headers.getlist('content-length')
        content_types = request.headers.getlist('content-type')
        status, code = 400, 'invalid_request'
        valid = (
            len(content_types) == 1
            and content_types[0].split(';', 1)[0].strip().lower() == 'application/json'
            and len(lengths) <= 1
        )
        if lengths and (not lengths[0].isdigit() or int(lengths[0]) > self._max):
            valid = False
            if lengths[0].isdigit() and int(lengths[0]) > self._max:
                status, code = 413, 'request_too_large'
        body = bytearray()
        if valid:
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body.extend(message.get('body', b''))
                if len(body) > self._max:
                    valid, status, code = False, 413, 'request_too_large'
                    break
                if not message.get('more_body', False):
                    break
            if lengths and int(lengths[0]) != len(body):
                valid = False
        if not valid:
            response = JSONResponse(
                {
                    'error': {
                        'message': 'Invalid request body',
                        'type': 'invalid_request_error',
                        'param': None,
                        'code': code,
                    }
                },
                status_code=status,
            )
            await response(scope, receive, send)
            return
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self._app(scope, replay, send)
