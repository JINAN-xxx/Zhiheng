from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


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
