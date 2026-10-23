from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestOriginMiddleware:
    """Inspect request metadata without wrapping or consuming the ASGI receive channel."""

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
            if origin is not None and origin.rstrip('/') not in allowed:
                response = JSONResponse(
                    {'error': {'code': 'forbidden_origin', 'message': '请求来源不受信任'}}, status_code=403
                )
                await response(scope, receive, send)
                return
            if not request.headers.get('content-type', '').startswith('application/json'):
                response = JSONResponse(
                    {'error': {'code': 'invalid_content_type', 'message': '请使用 JSON 请求'}}, status_code=415
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


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
