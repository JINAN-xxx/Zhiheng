"""Inference waiting, disconnect handling and SSE encoding over native runs."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import suppress
from time import monotonic
from uuid import UUID

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

from zhiheng.api.common.run_polling import RunPollInterval
from zhiheng.api.inference.errors import error_payload
from zhiheng.application.execution.timeouts import MODEL_TIMEOUT_CODES
from zhiheng.bootstrap.container import Container
from zhiheng.modules.developer.public import Scope as KeyScope
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


async def cancel(container: Container, run_id: UUID, owner_id: UUID) -> None:
    async with container.database.sessions.begin() as session:
        await container.public_runs.cancel(session, run_id, owner_id)


async def snapshots(
    container: Container, request: Request, run_id: UUID, owner_id: UUID, token: str, *, scope: KeyScope
) -> AsyncIterator[RunSnapshot]:
    terminal = False
    interval = RunPollInterval(0.05)
    deadline = monotonic() + container.settings.provider_total_timeout_seconds + 60
    try:
        while True:
            async with container.database.sessions() as session:
                identity = await container.keys.authenticate(session, token, scope=scope)
                snapshot = await container.runs.snapshot(session, run_id, owner_id)
                if scope == 'agents:invoke' and snapshot.revision != interval.revision:
                    if snapshot.resource_type != 'agent_invocation':
                        raise AppError('run_not_found', 'Run is unavailable', 404)
                    await container.agents.require_enabled_application(
                        session, Principal(identity.owner_id, '', False), snapshot.resource_id, identity.application_id
                    )
                    await container.keys.authorize_run(
                        session, run_id, scope=scope, agent_id=snapshot.resource_id, expected_identity=identity
                    )
                    await container.knowledge_preparation.require_run_bases(session, run_id, snapshot.resource_id)
            terminal = snapshot.status in TERMINAL_STATUSES
            changed = snapshot.revision != interval.revision
            delay = interval.next_delay(snapshot)
            if changed or terminal:
                yield snapshot
            if terminal:
                return
            if monotonic() > deadline:
                raise AppError('request_wait_timeout', '等待运行结果超过时限，请查询原运行状态', 504)
            if await request.is_disconnected():
                raise asyncio.CancelledError
            await asyncio.sleep(min(delay, max(0, deadline - monotonic())))
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
                raise terminal_error(snapshot)
            return snapshot
    raise AppError('provider_unavailable', 'Model request did not complete', 502)


def terminal_error(snapshot: RunSnapshot) -> AppError:
    if snapshot.error and snapshot.error.code in MODEL_TIMEOUT_CODES:
        return AppError(snapshot.error.code, snapshot.error.message, 504)
    if snapshot.error and snapshot.error.code == 'provider_insufficient_balance':
        return AppError(snapshot.error.code, snapshot.error.message, 502)
    return AppError('provider_unavailable', 'Model request did not complete', 502)


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
                error = terminal_error(snapshot)
                yield frame(error_payload(error.code, error.message, error.status_code))
    except AppError as error:
        yield frame(error_payload(error.code, error.message, error.status_code))


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
