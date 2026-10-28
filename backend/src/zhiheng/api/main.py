"""Constructing or importing the application never reads legacy configuration."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from zhiheng.api.errors import install_error_handlers
from zhiheng.api.middleware import PublicTransportMiddleware, RequestOriginMiddleware
from zhiheng.api.routes.agents import router as agents_router
from zhiheng.api.routes.auth import router as auth_router
from zhiheng.api.routes.chat import router as chat_router
from zhiheng.api.routes.developer import router as developer_router
from zhiheng.api.routes.files import router as files_router
from zhiheng.api.routes.knowledge import router as knowledge_router
from zhiheng.api.routes.knowledge_graph import router as knowledge_graph_router
from zhiheng.api.routes.message_actions import public_router as shared_messages_router
from zhiheng.api.routes.message_actions import router as message_actions_router
from zhiheng.api.routes.models import router as models_router
from zhiheng.api.routes.public import router as public_router
from zhiheng.api.routes.public_agents import router as public_agents_router
from zhiheng.api.routes.runs import router as runs_router
from zhiheng.api.routes.skills import router as skills_router
from zhiheng.api.routes.usage import router as usage_router
from zhiheng.api.routes.work import router as work_router
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
    app.include_router(auth_router)
    app.include_router(files_router)
    app.include_router(knowledge_router)
    app.include_router(knowledge_graph_router)
    app.include_router(agents_router)
    app.include_router(public_agents_router)
    app.include_router(models_router)
    app.include_router(skills_router)
    app.include_router(usage_router)
    app.include_router(public_router)
    app.include_router(chat_router)
    app.include_router(message_actions_router)
    app.include_router(shared_messages_router)
    app.include_router(developer_router)
    app.include_router(runs_router)
    app.include_router(work_router)

    app.add_middleware(PublicTransportMiddleware)
    app.add_middleware(RequestOriginMiddleware, allowed_origins=settings.allowed_origins)

    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.allowed_origins),
            allow_credentials=True,
            allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'],
            allow_headers=['Content-Type', 'Authorization'],
        )

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

    return app


def create_configured_app() -> FastAPI:
    """Explicit process factory used by the source-mode reload supervisor."""
    return create_app(Settings())
