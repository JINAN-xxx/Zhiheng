import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from zhiheng.shared.errors import AppError

logger = logging.getLogger(__name__)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, error: AppError) -> JSONResponse:
        return JSONResponse({'error': {'code': error.code, 'message': error.message}}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, _error: RequestValidationError) -> JSONResponse:
        # FastAPI's default response includes raw request input, including secrets.
        return JSONResponse({'error': {'code': 'invalid_request', 'message': '请求字段无效'}}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, error: Exception) -> JSONResponse:
        # Exception strings from provider and database libraries can contain credentials.
        logger.error('Unhandled request failure (%s)', type(error).__name__)
        return JSONResponse({'error': {'code': 'internal_error', 'message': '服务暂时不可用'}}, status_code=500)
