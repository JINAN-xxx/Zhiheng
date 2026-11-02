import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import suppress
from time import monotonic
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.types import Receive, Scope, Send

from zhiheng.api.dependencies import ContainerDependency
from zhiheng.api.public_errors import error_payload
from zhiheng.bootstrap.container import Container
from zhiheng.modules.developer.public import Scope as KeyScope
from zhiheng.modules.models.public import ModelMessage
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.shared.errors import AppError

"""Limited OpenAI-compatible protocol over durable platform runs."""


MAX_STOPS = 4
MAX_ITEMS = 256
MAX_ITEM_BYTES = 32768
MAX_QUERY_BYTES = 8192

router = APIRouter(prefix='/v1', tags=['public'], dependencies=[Depends(HTTPBearer(auto_error=False))])


class PublicBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: UUID
    user: str | None = Field(default=None, max_length=256)


class Message(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['system', 'developer', 'user', 'assistant']
    content: str = Field(min_length=1, max_length=64000)


class ChatBody(PublicBody):
    messages: list[Message] = Field(min_length=1, max_length=100)
    stream: bool = Field(default=False, strict=True)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, strict=True, gt=0, le=131072)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    stop: str | list[str] | None = None

    @field_validator('stop')
    @classmethod
    def validate_stop(cls, value: str | list[str] | None) -> str | list[str] | None:
        if isinstance(value, list) and (
            not 1 <= len(value) <= MAX_STOPS or any(not item or len(item) > MAX_ITEMS for item in value)
        ):
            raise ValueError('Invalid stop sequences')
        if isinstance(value, str) and (not value or len(value) > MAX_ITEMS):
            raise ValueError('Invalid stop sequence')
        return value


class EmbeddingBody(PublicBody):
    input: str | list[str]
    encoding_format: Literal['float'] = 'float'
    dimensions: int | None = Field(default=None, strict=True, ge=1, le=65536)

    @field_validator('input')
    @classmethod
    def validate_input(cls, value: str | list[str]) -> str | list[str]:
        values = [value] if isinstance(value, str) else value
        if not 1 <= len(values) <= MAX_ITEMS or any(not item or len(item.encode()) > MAX_ITEM_BYTES for item in values):
            raise ValueError('Invalid embedding input')
        return value


class Document(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str


class RerankBody(PublicBody):
    query: str = Field(min_length=1)
    documents: list[str | Document] = Field(min_length=1, max_length=256)
    top_n: int | None = Field(default=None, strict=True, ge=1)
    return_documents: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def validate_documents(self) -> 'RerankBody':
        if len(self.query.encode()) > MAX_QUERY_BYTES or (self.top_n is not None and self.top_n > len(self.documents)):
            raise ValueError('Invalid rerank input')
        if any(
            not (text := doc if isinstance(doc, str) else doc.text) or len(text.encode()) > MAX_ITEM_BYTES
            for doc in self.documents
        ):
            raise ValueError('Invalid rerank documents')
        return self


def bearer(request: Request) -> str:
    values = request.headers.getlist('authorization')
    if len(values) != 1 or re.fullmatch(r'Bearer sk-zh-[A-Za-z0-9_-]{43}', values[0]) is None:
        raise AppError('invalid_api_key', 'Invalid API key', 401)
    return values[0][7:]


@router.get('/models')
async def models(request: Request, container: ContainerDependency) -> dict[str, object]:
    token = bearer(request)
    await container.database.require_schema()
    async with container.database.sessions() as session:
        identity = await container.keys.authenticate(session, token)
        await container.keys.authorize_identity(session, identity, [], scope='models:invoke')
        key = await container.keys.info_for_identity(session, identity)
        rows = await container.models.list_models(session)
        visible = []
        for row in rows:
            if row.id not in key.model_ids:
                continue
            visible.append({'id': str(row.id), 'object': 'model', 'created': 0, 'owned_by': 'zhiheng'})
        return {'object': 'list', 'data': visible}


async def admit(
    container: Container,
    request: Request,
    operation: str,
    messages: tuple[ModelMessage, ...],
    body: PublicBody,
    options: dict[str, object],
) -> tuple[RunSnapshot, UUID, str]:
    token = bearer(request)
    await container.database.require_schema()
    async with container.database.sessions.begin() as session:
        run, identity = await container.public_runs.admit(
            session,
            token,
            model_id=body.model,
            operation=operation,
            messages=messages,
            options=options,
            token_limit=container.settings.token_limit,
        )
    return run, identity.owner_id, token


async def cancel(container: Container, run_id: UUID, owner_id: UUID) -> None:
    async with container.database.sessions.begin() as session:
        _, release = await container.runs.cancel(session, run_id, owner_id)
        if release:
            await container.usage.settle(session, run_id, rejected_before_execution=True)


async def snapshots(
    container: Container, request: Request, run_id: UUID, owner_id: UUID, token: str, *, scope: KeyScope
) -> AsyncIterator[RunSnapshot]:
    terminal = False
    deadline = monotonic() + container.settings.provider_timeout_seconds + 30
    try:
        while True:
            async with container.database.sessions() as session:
                identity = await container.keys.authenticate(session, token)
                await container.keys.authorize_identity(session, identity, [], scope=scope)
                snapshot = await container.runs.snapshot(session, run_id, owner_id)
            terminal = snapshot.status in TERMINAL_STATUSES
            yield snapshot
            if terminal:
                return
            if monotonic() > deadline:
                raise AppError('service_unavailable', 'Request execution timed out', 503)
            if await request.is_disconnected():
                raise asyncio.CancelledError
            await asyncio.sleep(0.05)
    finally:
        # Cancellation must also release an admitted run when disconnect happens before provider start.
        if not terminal:
            cleanup = asyncio.create_task(cancel(container, run_id, owner_id))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
                raise


def known_usage(run: RunSnapshot) -> dict[str, int | None]:
    left, right = run.usage.input_tokens, run.usage.output_tokens
    return {
        'prompt_tokens': left,
        'completion_tokens': right,
        'total_tokens': left + right if left is not None and right is not None else None,
    }


async def wait_result(
    container: Container, request: Request, run: RunSnapshot, owner_id: UUID, token: str, *, scope: KeyScope
) -> RunSnapshot:
    async for snapshot in snapshots(container, request, run.id, owner_id, token, scope=scope):
        if snapshot.status in TERMINAL_STATUSES:
            if snapshot.status != 'completed':
                raise AppError('provider_unavailable', 'Model request did not complete', 502)
            return snapshot
    raise AppError('provider_unavailable', 'Model request did not complete', 502)


def frame(value: dict[str, object] | str) -> str:
    return (
        'data: '
        + (json.dumps(value, ensure_ascii=False, separators=(',', ':')) if isinstance(value, dict) else value)
        + '\n\n'
    )


class LifecycleStream(StreamingResponse):
    def __init__(self, content: AsyncIterator[str], cleanup: Callable[[], Coroutine[object, object, None]]) -> None:
        super().__init__(
            content, media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )
        self.cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            task = asyncio.create_task(self.cleanup())
            with suppress(asyncio.CancelledError):
                await asyncio.shield(task)
            await task


async def chat_frames(
    container: Container,
    request: Request,
    run: RunSnapshot,
    owner_id: UUID,
    token: str,
    model: UUID,
    *,
    scope: KeyScope,
) -> AsyncIterator[str]:
    def chunk(delta: dict[str, object], finish: str | None = None) -> dict[str, object]:
        return {
            'id': 'chatcmpl-' + run.id.hex,
            'object': 'chat.completion.chunk',
            'created': int(run.created_at.timestamp()),
            'model': str(model),
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
        }

    yield frame(chunk({'role': 'assistant'}))
    sent = ''
    sent_reasoning = ''
    try:
        async for snapshot in snapshots(container, request, run.id, owner_id, token, scope=scope):
            if snapshot.reasoning != sent_reasoning:
                if not snapshot.reasoning.startswith(sent_reasoning):
                    raise AppError('provider_unavailable', 'Model request did not complete', 502)
                yield frame(chunk({'reasoning_content': snapshot.reasoning[len(sent_reasoning) :]}))
                sent_reasoning = snapshot.reasoning
            if snapshot.text != sent:
                if not snapshot.text.startswith(sent):
                    raise AppError('provider_unavailable', 'Model request did not complete', 502)
                yield frame(chunk({'content': snapshot.text[len(sent) :]}))
                sent = snapshot.text
            if snapshot.status == 'completed':
                yield frame(chunk({}, snapshot.finish_reason or 'stop'))
                yield frame('[DONE]')
            elif snapshot.status in TERMINAL_STATUSES:
                yield frame(error_payload('provider_unavailable', 'Model request did not complete', 502))
    except AppError as error:
        yield frame(error_payload(error.code, error.message, error.status_code))


@router.post('/chat/completions', response_model=None)
async def chat(body: ChatBody, request: Request, container: ContainerDependency) -> JSONResponse | StreamingResponse:
    messages = tuple(ModelMessage(message.role, message.content) for message in body.messages)
    options = body.model_dump(exclude={'model', 'messages', 'stream'}, exclude_none=True)
    run, owner_id, token = await admit(container, request, 'chat', messages, body, options)
    return await chat_response(
        container, request, run, owner_id, token, body.model, stream=body.stream, scope='models:invoke'
    )


async def chat_response(
    container: Container,
    request: Request,
    run: RunSnapshot,
    owner_id: UUID,
    token: str,
    model: UUID,
    *,
    stream: bool,
    scope: KeyScope,
) -> JSONResponse | StreamingResponse:
    if stream:
        return LifecycleStream(
            chat_frames(container, request, run, owner_id, token, model, scope=scope),
            lambda: cancel(container, run.id, owner_id),
        )
    result = await wait_result(container, request, run, owner_id, token, scope=scope)
    return JSONResponse(
        {
            'id': 'chatcmpl-' + run.id.hex,
            'object': 'chat.completion',
            'created': int(run.created_at.timestamp()),
            'model': str(model),
            'choices': [
                {
                    'index': 0,
                    'message': {
                        'role': 'assistant',
                        'content': result.text,
                        **({'reasoning_content': result.reasoning} if result.reasoning else {}),
                    },
                    'finish_reason': result.finish_reason or 'stop',
                }
            ],
            'usage': known_usage(result),
        }
    )


@router.post('/embeddings')
async def embeddings(body: EmbeddingBody, request: Request, container: ContainerDependency) -> JSONResponse:
    values = [body.input] if isinstance(body.input, str) else body.input
    run, owner_id, token = await admit(
        container,
        request,
        'embedding',
        tuple(ModelMessage('user', value) for value in values),
        body,
        body.model_dump(include={'dimensions', 'user'}, exclude_none=True),
    )
    result = await wait_result(container, request, run, owner_id, token, scope='models:invoke')
    return JSONResponse(
        {'object': 'list', 'model': str(body.model), **(result.result or {}), 'usage': known_usage(result)}
    )


@router.post('/rerank')
async def rerank(body: RerankBody, request: Request, container: ContainerDependency) -> JSONResponse:
    messages = (
        ModelMessage('user', body.query),
        *(ModelMessage('user', value if isinstance(value, str) else value.text) for value in body.documents),
    )
    options = body.model_dump(include={'top_n', 'return_documents'}, exclude_none=True)
    run, owner_id, token = await admit(container, request, 'rerank', messages, body, options)
    result = await wait_result(container, request, run, owner_id, token, scope='models:invoke')
    return JSONResponse({'id': str(run.id), 'model': str(body.model), **(result.result or {})})
