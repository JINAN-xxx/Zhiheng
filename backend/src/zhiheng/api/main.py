"""Constructing or importing the application never reads legacy configuration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from zhiheng.api.common.cors import ResourceCORSMiddleware
from zhiheng.api.common.errors import install_error_handlers
from zhiheng.api.common.middleware import PublicTransportMiddleware, RequestOriginMiddleware
from zhiheng.api.common.openapi import install_openapi
from zhiheng.api.common.resource_protocol import ResourceProtocolMiddleware
from zhiheng.api.console.router import router as console_router
from zhiheng.api.inference.router import router as inference_router
from zhiheng.api.resources.router import router as resources_router
from zhiheng.bootstrap.container import Container
from zhiheng.bootstrap.settings import Settings

WORKER_HEARTBEAT_MAX_AGE_SECONDS = 15


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = Container(settings)
        app.state.container = container
        try:
            yield
        finally:
            await container.close()
            del app.state.container

    app = FastAPI(title='智衡 AI', version='0.1.0', lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(console_router)
    app.include_router(resources_router)
    app.include_router(inference_router)

    app.add_middleware(PublicTransportMiddleware)
    app.add_middleware(RequestOriginMiddleware, allowed_origins=settings.allowed_origins)

    if settings.allowed_origins:
        app.add_middleware(
            ResourceCORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=True,
            allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
            allow_headers=['Content-Type', 'Authorization', 'Idempotency-Key', 'If-Match', 'Range'],
            expose_headers=['ETag', 'Location', 'Retry-After', 'X-Request-ID', 'Content-Range', 'Accept-Ranges'],
        )

    app.add_middleware(ResourceProtocolMiddleware)

    @app.get('/health/live')
    async def live() -> dict[str, str]:
        return {'status': 'ok'}

    @app.get('/health/ready')
    async def ready(request: Request) -> JSONResponse:
        container: Container = request.app.state.container
        try:
            await container.database.require_schema()
            await container.redis.ping()
            heartbeat = await container.redis.get(settings.worker_health_key)
            if heartbeat is None or time() - float(heartbeat) > WORKER_HEARTBEAT_MAX_AGE_SECONDS:
                return JSONResponse({'status': 'unavailable', 'component': 'worker'}, status_code=503)
        except Exception:
            return JSONResponse({'status': 'unavailable'}, status_code=503)
        return JSONResponse({'status': 'ok'})

    install_openapi(app)
    return app


def create_configured_app() -> FastAPI:
    """Explicit process factory used by the source-mode reload supervisor."""
    return create_app(Settings())
