"""Keep native CORS admission and headers while adapting resource preflight errors."""

from http import HTTPStatus

from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from zhiheng.api.common.resource_protocol import is_resource, resource_error


class ResourceCORSMiddleware(CORSMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] == 'http' and is_resource(scope) and scope['method'] == 'OPTIONS':
            request = Request(scope)
            if 'origin' in request.headers and 'access-control-request-method' in request.headers:
                response = self.preflight_response(request_headers=request.headers)
                if response.status_code == HTTPStatus.BAD_REQUEST:
                    headers = {
                        name: value
                        for name, value in response.headers.items()
                        if name not in {'content-length', 'content-type'}
                    }
                    response = resource_error(
                        request, 'cors_preflight_rejected', '请求CORS预检被拒绝', response.status_code, headers=headers
                    )
                await response(scope, receive, send)
                return
        await super().__call__(scope, receive, send)
