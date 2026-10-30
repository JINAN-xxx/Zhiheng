import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from zhiheng.api.public_errors import error_payload
from zhiheng.shared.errors import AppError

logger = logging.getLogger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, error: AppError) -> JSONResponse:
        if _request.url.path.startswith('/v1/'):
            return JSONResponse(
                error_payload(error.code, error.message, error.status_code),
                status_code=error.status_code,
                headers={'WWW-Authenticate': 'Bearer'} if error.status_code == status.HTTP_401_UNAUTHORIZED else None,
            )
        payload: dict[str, object] = {'code': error.code, 'message': error.message}
        if error.details is not None:
            payload['details'] = error.details
        return JSONResponse({'error': payload}, status_code=error.status_code)

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        if request.url.path.startswith('/v1/'):
            return JSONResponse(
                error_payload(
                    'not_found' if error.status_code == status.HTTP_404_NOT_FOUND else 'invalid_request',
                    'Not found' if error.status_code == status.HTTP_404_NOT_FOUND else 'Invalid request',
                    error.status_code,
                ),
                status_code=error.status_code,
                headers=error.headers,
            )
        return JSONResponse({'detail': error.detail}, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, _error: RequestValidationError) -> JSONResponse:
        if _request.url.path.startswith('/v1/'):
            return JSONResponse(error_payload('invalid_request', 'Invalid request fields', 400), status_code=400)
        # FastAPI's default response includes raw request input, including secrets.
        return JSONResponse({'error': {'code': 'invalid_request', 'message': '请求字段无效'}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, error: Exception) -> JSONResponse:
        # Exception strings from provider and database libraries can contain credentials.
        logger.error('Unhandled request failure (%s)', type(error).__name__)
        if _request.url.path.startswith('/v1/'):
            return JSONResponse(error_payload('internal_error', 'Service unavailable', 500), status_code=500)
        return JSONResponse({'error': {'code': 'internal_error', 'message': '服务暂时不可用'}}, status_code=500)
