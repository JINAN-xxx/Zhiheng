import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from common.aspect.db_seesion import DBSessionDependency
from common.router import APIRouterPro
from plugins.ai.config import LiteLLMSettings
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.entity.vo.ai_openai_compat_vo import (
    OpenAIChatCompletionRequestModel,
    OpenAIEmbeddingRequestModel,
    OpenAIRerankRequestModel,
)
from plugins.ai.infrastructure.public_api_provider import PublicApiServices, get_public_api_services
from plugins.ai.public import api_transport as public_transport

openai_compat_controller = APIRouterPro(prefix='/v1', order_num=30, tags=['Developer Public API'])
_settings_factory: Callable[[], LiteLLMSettings] = LiteLLMSettings
_public_api_services = get_public_api_services


@dataclass(frozen=True, slots=True)
class _ControllerOutcome:
    response: Response | None = None
    cancelled: bool = False


def _finish_controller_outcome(outcome: _ControllerOutcome) -> Response:
    if outcome.cancelled:
        raise asyncio.CancelledError from None
    return cast('Response', outcome.response)


@openai_compat_controller.get('/models', summary='List authorized public models')
async def list_public_models(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    operation = _handle_models
    outcome = await operation(request, query_db)
    del request, query_db, operation
    return _finish_controller_outcome(outcome)


@openai_compat_controller.post(
    '/chat/completions',
    summary='Create an OpenAI-compatible chat completion',
    responses={200: {'content': {'application/json': {}, public_transport.OPENAI_SSE_MEDIA_TYPE: {}}}},
)
async def create_chat_completion(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    operation = _handle_chat
    outcome = await operation(request, query_db)
    del request, query_db, operation
    return _finish_controller_outcome(outcome)


@openai_compat_controller.post('/embeddings', summary='Create an OpenAI-compatible embedding')
async def create_embeddings(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    operation = _handle_embeddings
    outcome = await operation(request, query_db)
    del request, query_db, operation
    return _finish_controller_outcome(outcome)


@openai_compat_controller.post('/rerank', summary='Create a LiteLLM/Cohere-compatible rerank')
async def create_rerank(
    request: Request,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    operation = _handle_rerank
    outcome = await operation(request, query_db)
    del request, query_db, operation
    return _finish_controller_outcome(outcome)


async def _handle_models(request: Request, db: AsyncSession) -> _ControllerOutcome:
    async def operation(settings: LiteLLMSettings, services: PublicApiServices) -> Response:
        identity = await public_transport.authenticate_request(request, services.authenticator)
        result = await services.entitlement.list_models(identity)
        return JSONResponse(content=result.model_dump(mode='json'))

    return await _run_request(request, db, operation)


async def _handle_chat(request: Request, db: AsyncSession) -> _ControllerOutcome:
    async def operation(settings: LiteLLMSettings, services: PublicApiServices) -> Response:
        identity = await public_transport.authenticate_request(request, services.authenticator)
        body_outcome = await public_transport.read_json_request(
            request, settings.api_max_request_bytes, OpenAIChatCompletionRequestModel
        )
        if body_outcome.cancelled:
            raise asyncio.CancelledError
        if body_outcome.value is None:
            raise PublicApiError.invalid_request()
        chat_request = body_outcome.value
        if chat_request.stream:
            stream = await services.compat.stream(identity, chat_request)
            return public_transport.streaming_response(stream)
        result = await services.compat.complete(identity, chat_request)
        return JSONResponse(content=result.model_dump(mode='json'))

    return await _run_request(request, db, operation)


async def _handle_embeddings(request: Request, db: AsyncSession) -> _ControllerOutcome:
    async def operation(settings: LiteLLMSettings, services: PublicApiServices) -> Response:
        identity = await public_transport.authenticate_request(request, services.authenticator)
        body_outcome = await public_transport.read_json_request(
            request, settings.api_max_request_bytes, OpenAIEmbeddingRequestModel
        )
        if body_outcome.cancelled:
            raise asyncio.CancelledError
        if body_outcome.value is None:
            raise PublicApiError.invalid_request()
        result = await services.compat.embed(identity, body_outcome.value)
        return JSONResponse(content=result.model_dump(mode='json'))

    return await _run_request(request, db, operation)


async def _handle_rerank(request: Request, db: AsyncSession) -> _ControllerOutcome:
    async def operation(settings: LiteLLMSettings, services: PublicApiServices) -> Response:
        identity = await public_transport.authenticate_request(request, services.authenticator)
        body_outcome = await public_transport.read_json_request(
            request, settings.api_max_request_bytes, OpenAIRerankRequestModel
        )
        if body_outcome.cancelled:
            raise asyncio.CancelledError
        if body_outcome.value is None:
            raise PublicApiError.invalid_request()
        result = await services.compat.rerank(identity, body_outcome.value)
        return JSONResponse(content=result.model_dump(mode='json'))

    return await _run_request(request, db, operation)


async def _run_request(
    request: Request,
    db: AsyncSession,
    operation: Callable[[LiteLLMSettings, PublicApiServices], Awaitable[Response]],
) -> _ControllerOutcome:
    try:
        settings = _settings_factory()
        if not settings.public_api_enabled:
            return _ControllerOutcome(response=public_transport.error_response(PublicApiError.feature_disabled()))
        services = await _public_api_services(request, db, settings)
        return _ControllerOutcome(response=await operation(settings, services))
    except asyncio.CancelledError:
        return _ControllerOutcome(cancelled=True)
    except PublicApiError as error:
        return _ControllerOutcome(response=public_transport.error_response(error))
    except Exception:
        return _ControllerOutcome(response=public_transport.error_response(PublicApiError.service_unavailable()))
