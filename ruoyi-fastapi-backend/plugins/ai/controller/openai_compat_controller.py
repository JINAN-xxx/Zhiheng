import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, TypeVar, cast

from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
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
from plugins.ai.infrastructure.developer_key_authenticator import (
    DeveloperApiIdentity,
    DeveloperKeyAuthenticator,
    require_authenticated_identity,
)
from plugins.ai.infrastructure.openai_stream_adapter import OpenAIStreamAdapter
from plugins.ai.infrastructure.public_api_provider import PublicApiServices, get_public_api_services

ResultT = TypeVar('ResultT')
_INVALID_HEADER = object()
OPENAI_SSE_MEDIA_TYPE = 'text/event-stream'
OPENAI_SSE_HEADERS = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
HTTP_UNAUTHORIZED = 401

openai_compat_controller = APIRouterPro(prefix='/v1', order_num=30, tags=['Developer Public API'])
_settings_factory: Callable[[], LiteLLMSettings] = LiteLLMSettings
_public_api_services = get_public_api_services


@dataclass(frozen=True, slots=True)
class _ControllerOutcome:
    response: Response | None = None
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class _BodyOutcome:
    value: object | None = None
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
    responses={200: {'content': {'application/json': {}, OPENAI_SSE_MEDIA_TYPE: {}}}},
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
        identity = await _authenticate_request(request, services.authenticator)
        result = await services.entitlement.list_models(identity)
        return JSONResponse(content=result.model_dump(mode='json'))

    return await _run_request(request, db, operation)


async def _handle_chat(request: Request, db: AsyncSession) -> _ControllerOutcome:
    async def operation(settings: LiteLLMSettings, services: PublicApiServices) -> Response:
        identity = await _authenticate_request(request, services.authenticator)
        body_outcome = await _read_request(request, settings.api_max_request_bytes, OpenAIChatCompletionRequestModel)
        if body_outcome.cancelled:
            raise asyncio.CancelledError
        if body_outcome.value is None:
            raise PublicApiError.invalid_request()
        chat_request = body_outcome.value
        if chat_request.stream:
            stream = await services.compat.stream(identity, chat_request)
            return StreamingResponse(
                _stream_bytes(stream),
                media_type=OPENAI_SSE_MEDIA_TYPE,
                headers=OPENAI_SSE_HEADERS,
            )
        result = await services.compat.complete(identity, chat_request)
        return JSONResponse(content=result.model_dump(mode='json'))

    return await _run_request(request, db, operation)


async def _handle_embeddings(request: Request, db: AsyncSession) -> _ControllerOutcome:
    async def operation(settings: LiteLLMSettings, services: PublicApiServices) -> Response:
        identity = await _authenticate_request(request, services.authenticator)
        body_outcome = await _read_request(request, settings.api_max_request_bytes, OpenAIEmbeddingRequestModel)
        if body_outcome.cancelled:
            raise asyncio.CancelledError
        if body_outcome.value is None:
            raise PublicApiError.invalid_request()
        result = await services.compat.embed(identity, body_outcome.value)
        return JSONResponse(content=result.model_dump(mode='json'))

    return await _run_request(request, db, operation)


async def _handle_rerank(request: Request, db: AsyncSession) -> _ControllerOutcome:
    async def operation(settings: LiteLLMSettings, services: PublicApiServices) -> Response:
        identity = await _authenticate_request(request, services.authenticator)
        body_outcome = await _read_request(request, settings.api_max_request_bytes, OpenAIRerankRequestModel)
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
            return _ControllerOutcome(response=_error_response(PublicApiError.feature_disabled()))
        services = await _public_api_services(request, db, settings)
        return _ControllerOutcome(response=await operation(settings, services))
    except asyncio.CancelledError:
        return _ControllerOutcome(cancelled=True)
    except PublicApiError as error:
        return _ControllerOutcome(response=_error_response(error))
    except Exception:
        return _ControllerOutcome(response=_error_response(PublicApiError.service_unavailable()))


async def _authenticate_request(
    request: Request,
    authenticator: DeveloperKeyAuthenticator,
) -> DeveloperApiIdentity:
    authorization = _single_authorization(request)
    outcome = await authenticator.authenticate(authorization, now=datetime.now(timezone.utc))
    del authorization
    return require_authenticated_identity(outcome)


def _single_authorization(request: Request) -> str | None:
    values = _raw_header_values(request, b'authorization')
    if len(values) != 1:
        return None
    try:
        return values[0].decode('latin-1')
    except Exception:
        return None


async def _read_request(
    request: Request,
    max_bytes: int,
    model_type: type[object],
) -> _BodyOutcome:
    try:
        content_type = _single_header(request, b'content-type')
        if content_type is _INVALID_HEADER or not isinstance(content_type, str):
            return _BodyOutcome()
        if content_type.split(';', 1)[0].strip().lower() != 'application/json':
            return _BodyOutcome()
        content_length = _content_length(request)
        if content_length is _INVALID_HEADER or (isinstance(content_length, int) and content_length > max_bytes):
            return _BodyOutcome()
        body = bytearray()
        async for chunk in request.stream():
            if not isinstance(chunk, bytes) or len(chunk) > max_bytes - len(body):
                return _BodyOutcome()
            body.extend(chunk)
        if isinstance(content_length, int) and content_length != len(body):
            return _BodyOutcome()
        payload = json.loads(body)
        value = model_type.model_validate(payload)
        return _BodyOutcome(value=value)
    except asyncio.CancelledError:
        return _BodyOutcome(cancelled=True)
    except (UnicodeError, ValueError, TypeError, ValidationError):
        return _BodyOutcome()
    except Exception:
        return _BodyOutcome()


def _content_length(request: Request) -> int | object | None:
    value = _single_header(request, b'content-length')
    if value is None or value is _INVALID_HEADER:
        return value
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        return _INVALID_HEADER
    return int(value)


def _single_header(request: Request, name: bytes) -> str | object | None:
    values = _raw_header_values(request, name)
    if not values:
        return None
    if len(values) != 1:
        return _INVALID_HEADER
    try:
        return values[0].decode('ascii')
    except UnicodeDecodeError:
        return _INVALID_HEADER


def _raw_header_values(request: Request, name: bytes) -> list[bytes]:
    headers = request.scope.get('headers', ())
    return [value for raw_name, value in headers if raw_name.lower() == name]


def _error_response(error: PublicApiError) -> JSONResponse:
    headers = {'WWW-Authenticate': 'Bearer'} if error.status_code == HTTP_UNAUTHORIZED else None
    return JSONResponse(status_code=error.status_code, content=error.public_dict(), headers=headers)


async def _stream_bytes(stream: OpenAIStreamAdapter) -> AsyncIterator[bytes]:
    active_error: BaseException | None = None
    try:
        async for frame in stream:
            yield frame
    except BaseException as error:
        active_error = error
        raise
    finally:
        try:
            await stream.aclose()
        except BaseException:
            if active_error is None:
                raise
