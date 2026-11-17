"""The execution path for every admitted model call, including connection tests."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, suppress
from dataclasses import dataclass
from http import HTTPStatus
from time import monotonic
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zhiheng.application.execution.failures import database_sqlstate, exception_frames, execution_error
from zhiheng.application.execution.lifecycle import RunLifecycle
from zhiheng.modules.billing.public import UsageService
from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.models.public import (
    AgentModelProvider,
    ModelBinding,
    ModelDelta,
    ModelProvider,
    ModelRequest,
    ProviderError,
)
from zhiheng.runtime.contracts import ClaimedRun, RunError, RunSnapshot, RunStatus, RunUsage
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.text import storage_text
from zhiheng.shared.time import utcnow

MAX_PROJECTION_BYTES = 2 * 1024 * 1024
PROJECTION_INTERVAL_SECONDS = 0.05
logger = logging.getLogger(__name__)


def retrieval_provider_error(claim: ClaimedRun, error: ProviderError) -> RunError:
    """Persist the safe fallback category so recovery never guesses from error text."""
    original = RunError(code=error.code, message=error.message)
    if claim.resource_type not in {'knowledge_rerank', 'knowledge_graph'}:
        return original
    try:
        request = ModelRequest.model_validate(claim.payload)
    except ValueError:
        return original
    transient = error.reason != 'insufficient_balance' and (
        error.reason in {'timeout', 'transport_error'}
        if error.upstream_status is None
        else error.upstream_status == HTTPStatus.TOO_MANY_REQUESTS
        or error.upstream_status >= HTTPStatus.INTERNAL_SERVER_ERROR
    )
    code = (
        'knowledge_rerank_transient'
        if claim.resource_type in {'knowledge_rerank', 'knowledge_graph'}
        and request.binding.model_type == 'rerank'
        and transient
        else error.code
    )
    return RunError(code=code, message=error.message)


def retrieval_execution_error(claim: ClaimedRun, error: Exception, *, provider_started: bool) -> RunError:
    if isinstance(error, TimeoutError) and provider_started:
        classified = retrieval_provider_error(claim, ProviderError(reason='timeout'))
        if classified.code == 'knowledge_rerank_transient':
            return classified
    if isinstance(error, TimeoutError) and provider_started and claim.resource_type == 'knowledge_embedding':
        return RunError(
            code='embedding_timeout', message='向量服务响应超时；原文件已保留，可重试处理。原调用用量待核对'
        )
    return execution_error(error, provider_started=provider_started)


@dataclass
class InferenceProgress:
    text: str = ''
    reasoning: str = ''
    raw_text: str = ''
    raw_reasoning: str = ''
    projection_bytes: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    agent_result: dict[str, object] | None = None
    provider_started_at: float | None = None
    provider_started: bool = False
    completion_seen: bool = False
    finish_reason: str | None = None
    result: dict[str, object] | None = None
    search_units: int | None = None
    cache_tokens: int | None = None
    reasoning_tokens: int | None = None

    def append(self, chunk: ModelDelta, *, agent_execution: bool, tool_execution: bool = False) -> None:
        text, reasoning = storage_text(chunk.text), storage_text(chunk.reasoning)
        chunk_bytes = len(text.encode('utf-8')) + len(reasoning.encode('utf-8'))
        if self.projection_bytes + chunk_bytes > MAX_PROJECTION_BYTES:
            raise ProviderError(reason='projection_limit')
        self.projection_bytes += chunk_bytes
        self.text += text
        self.reasoning += reasoning
        if tool_execution:
            self.raw_text += chunk.text
            self.raw_reasoning += chunk.reasoning
        for name in ('input_tokens', 'output_tokens', 'search_units', 'cache_tokens', 'reasoning_tokens'):
            value = getattr(chunk, name)
            if value is not None:
                setattr(self, name, value)
        if chunk.finish_reason is not None:
            self.completion_seen, self.finish_reason = True, chunk.finish_reason
        if chunk.result is not None:
            if agent_execution:
                self.agent_result = chunk.result
            else:
                self.result = chunk.result


class AgentExecution(Protocol):
    def provider(
        self,
        claim: ClaimedRun,
        request: ModelRequest,
        provider: ModelProvider,
        on_model_start: Callable[[], Awaitable[None]],
    ) -> AbstractAsyncContextManager[ModelProvider]: ...

    async def finish(
        self,
        session: AsyncSession,
        snapshot: RunSnapshot,
        result: dict[str, object] | None,
    ) -> None: ...


class InferenceExecutor:
    def __init__(  # noqa: PLR0913 - explicit model/agent handlers and native execution resources
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        runs: RunStore,
        usage: UsageService,
        chat: ChatService,
        provider_factory: Callable[[ModelBinding], Awaitable[ModelProvider]],
        lease_seconds: int,
        timeout_seconds: float,
        lifecycle: RunLifecycle | None = None,
        before_provider: Callable[[ClaimedRun], Awaitable[None]] | None = None,
        chat_agent: AgentExecution | None = None,
        assistant_agent: AgentExecution | None = None,
    ) -> None:
        self._sessions, self._runs, self._usage, self._chat = sessions, runs, usage, chat
        self._lifecycle = lifecycle or RunLifecycle(chat)
        self._provider_factory, self._lease_seconds, self._timeout = provider_factory, lease_seconds, timeout_seconds
        self._before_provider = before_provider
        self._agents = {
            'chat': chat_agent,
            'agent_chat': assistant_agent,
            'agent_preview': assistant_agent,
            'agent_invocation': assistant_agent,
        }

    async def _claim(self, run_id: UUID) -> ClaimedRun | None:
        try:
            async with self._sessions.begin() as session:
                claim = await self._runs.claim(session, run_id, self._lease_seconds)
                if claim is not None:
                    await self._lifecycle.project(session, await self._runs.projection(session, run_id))
        except DBAPIError as exc:
            self._record_failure(exc, run_id, None, 'claim')
            # The claim transaction rolled back. Outbox recovery still owns the pending run.
            raise RuntimeError('Execution claim failed; see correlated diagnostics') from None
        return claim

    async def execute(self, run_id: UUID) -> None:
        claim = await self._claim(run_id)
        if claim is None:
            return
        progress = InferenceProgress()
        consume = asyncio.create_task(self._consume(claim, progress))
        observe = asyncio.create_task(self._observe(claim, progress))
        status: RunStatus = 'completed'
        error: RunError | None = None
        rejected = False
        try:
            done, _ = await asyncio.wait({consume, observe}, return_when=asyncio.FIRST_COMPLETED)
            if observe in done:
                reason = observe.result()
                consume.cancel()
                with suppress(asyncio.CancelledError):
                    await consume
                if reason == 'lost':
                    return
                status = 'cancelled'
            else:
                await consume
        except ProviderError as exc:
            self._record_provider_failure(exc, claim.id, claim.attempt_id)
            status, rejected = 'failed', exc.definitive_rejection
            error = retrieval_provider_error(claim, exc)
        except asyncio.CancelledError:
            consume.cancel()
            observe.cancel()
            await asyncio.gather(consume, observe, return_exceptions=True)
            await self._shutdown(claim, progress)
            raise
        except Exception as exc:
            stage = 'provider' if progress.provider_started else 'before_provider'
            self._record_failure(exc, claim.id, claim.attempt_id, stage)
            status = 'failed'
            error = retrieval_execution_error(claim, exc, provider_started=progress.provider_started)
        finally:
            consume.cancel()
            observe.cancel()
            await asyncio.gather(consume, observe, return_exceptions=True)
        try:
            await self._finish(claim, progress, status, error, rejected=rejected)
        except DBAPIError as exc:
            self._record_failure(exc, claim.id, claim.attempt_id, 'settlement')
            # Keep the durable provider marker and reservation for lease recovery.
            raise RuntimeError('Execution settlement failed; recovery required') from None
        except AppError as exc:
            if exc.code != 'execution_lost':
                raise
            logger.info(
                'Discarded a result after execution ownership expired run_id=%s attempt_id=%s',
                claim.id,
                claim.attempt_id,
                extra={'run_id': str(claim.id), 'attempt_id': str(claim.attempt_id)},
            )

    @staticmethod
    def _record_provider_failure(error: ProviderError, run_id: UUID, attempt_id: UUID) -> None:
        logger.warning(
            'Model provider failed run_id=%s attempt_id=%s provider_reason=%s upstream_status=%s upstream_error_type=%s rate_limit_metric=%s',
            run_id,
            attempt_id,
            error.reason,
            error.upstream_status,
            error.upstream_error_type,
            error.rate_limit_metric,
            extra={
                'run_id': str(run_id),
                'attempt_id': str(attempt_id),
                'provider_reason': error.reason,
                'upstream_status': error.upstream_status,
                'upstream_error_type': error.upstream_error_type,
                'rate_limit_metric': error.rate_limit_metric,
            },
        )

    @staticmethod
    def _record_failure(error: Exception, run_id: UUID, attempt_id: UUID | None, stage: str) -> None:
        sqlstate, frames = database_sqlstate(error), exception_frames(error)
        logger.error(
            'Execution failed run_id=%s attempt_id=%s stage=%s error_type=%s sqlstate=%s frames=%s',
            run_id,
            attempt_id,
            stage,
            type(error).__name__,
            sqlstate,
            frames,
            extra={
                'run_id': str(run_id),
                'attempt_id': str(attempt_id) if attempt_id is not None else None,
                'error_type': type(error).__name__,
                'execution_stage': stage,
                'sqlstate': sqlstate,
                'exception_frames': frames,
            },
        )

    async def _shutdown(self, claim: ClaimedRun, progress: InferenceProgress) -> None:
        finalizer = asyncio.create_task(
            self._finish(
                claim,
                progress,
                'interrupted',
                RunError(code='worker_stopped', message='执行进程已停止'),
                rejected=False,
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(finalizer), timeout=3)
        except Exception as exc:
            logger.warning(
                'Shutdown finalization did not commit run_id=%s attempt_id=%s error_type=%s',
                claim.id,
                claim.attempt_id,
                type(exc).__name__,
                extra={'run_id': str(claim.id), 'attempt_id': str(claim.attempt_id), 'error_type': type(exc).__name__},
            )
        finally:
            finalizer.cancel()
            await asyncio.gather(finalizer, return_exceptions=True)

    async def _consume(self, claim: ClaimedRun, progress: InferenceProgress) -> None:
        request = ModelRequest.model_validate(claim.payload)
        async with self._sessions() as session:
            await self._usage.require_reservation(
                session, claim.id, claim.billing_owner_id or claim.owner_id, request.binding.model_id
            )
        if self._before_provider is not None:
            await self._before_provider(claim)
        provider = await self._provider_factory(request.binding)
        if request.agent_messages and (
            claim.resource_type != 'work_agent_model' or not callable(getattr(provider, 'stream_agent', None))
        ):
            raise ProviderError(definitive_rejection=True, reason='invalid_options')

        async def start_model() -> None:
            if progress.provider_started:
                raise AppError('execution_already_started', '本次模型调用已经开始', 409)
            if self._before_provider is not None:
                await self._before_provider(claim)
            async with self._sessions.begin() as session:
                if request.execution_deadline is not None and request.execution_deadline <= utcnow():
                    raise AppError('execution_deadline_exceeded', '模型执行期限已过', 409)
                started = await self._runs.start_provider(session, claim.id, claim.attempt_id)
            if not started:
                raise AppError('execution_lost', '执行权已失效', 409)
            progress.provider_started = True
            progress.provider_started_at = monotonic()

        async with AsyncExitStack() as resources:
            handler = self._agents.get(claim.resource_type)
            agent_execution = claim.resource_type in self._agents
            if agent_execution:
                if handler is None:
                    raise AppError('agent_execution_unavailable', '会话执行服务未装配', 503)
                provider = await resources.enter_async_context(handler.provider(claim, request, provider, start_model))
            else:
                await start_model()
            last_projection = 0.0

            async def read_stream() -> None:
                nonlocal last_projection
                stream = (
                    cast('AgentModelProvider', provider).stream_agent(request.agent_messages, request.tools)
                    if request.agent_messages
                    else provider.stream(request.messages)
                )
                async for chunk in stream:
                    progress.append(chunk, agent_execution=agent_execution, tool_execution=bool(request.agent_messages))
                    if (chunk.text or chunk.reasoning) and monotonic() - last_projection > PROJECTION_INTERVAL_SECONDS:
                        async with self._sessions.begin() as session:
                            if not await self._runs.project(
                                session, claim.id, claim.attempt_id, progress.text, progress.reasoning
                            ):
                                raise AppError('execution_lost', '执行权已失效', 409)
                            await self._lifecycle.project(session, await self._runs.projection(session, claim.id))
                        last_projection = monotonic()
                if not progress.completion_seen:
                    raise ProviderError(reason='incomplete_stream')
                if (
                    claim.resource_type in {'chat', 'agent_chat', 'agent_preview', 'agent_invocation'}
                    and progress.finish_reason == 'stop'
                    and not progress.text.strip()
                ):
                    raise AppError('chat_empty_response', '模型未返回有效正文，请重新生成或重试', 502)

            total_timeout = self._timeout * 2 + 10 if claim.resource_type in {'chat', 'agent_chat'} else self._timeout
            deadline_remaining = (
                (request.execution_deadline - utcnow()).total_seconds() if request.execution_deadline else total_timeout
            )
            await asyncio.wait_for(
                read_stream(),
                timeout=min(
                    total_timeout, request.execution_timeout_seconds or total_timeout, max(0.001, deadline_remaining)
                ),
            )

    async def _observe(self, claim: ClaimedRun, progress: InferenceProgress | None = None) -> str:
        while True:
            await asyncio.sleep(0.2)
            if (
                progress is not None
                and progress.provider_started_at is not None
                and monotonic() - progress.provider_started_at >= self._timeout
            ):
                raise TimeoutError('Provider execution deadline exceeded')
            async with self._sessions.begin() as session:
                state = await self._runs.control(session, claim.id, claim.attempt_id, self._lease_seconds)
            if state != 'active':
                return state

    async def _finish(
        self,
        claim: ClaimedRun,
        progress: InferenceProgress,
        status: RunStatus,
        error: RunError | None,
        *,
        rejected: bool,
    ) -> None:
        result = progress.result
        if claim.resource_type == 'work_agent_model':
            result = {
                'agent_message_json': json.dumps(
                    {
                        'role': 'assistant',
                        'content': progress.raw_text,
                        'reasoning_content': progress.raw_reasoning,
                        'tool_calls': (progress.result or {}).get('tool_calls', []),
                    },
                    ensure_ascii=True,
                )
            }
        async with self._sessions.begin() as session:
            await self._runs.lock_execution(session, claim.id, claim.attempt_id)
            outcome = await self._usage.settle(
                session,
                claim.id,
                input_tokens=progress.input_tokens,
                output_tokens=progress.output_tokens,
                rejected_before_execution=rejected or not progress.provider_started,
                search_units=progress.search_units,
                cache_tokens=progress.cache_tokens,
                reasoning_tokens=progress.reasoning_tokens,
            )
            snapshot = await self._runs.finish(
                session,
                claim.id,
                claim.attempt_id,
                status=status,
                content=progress.text,
                reasoning=progress.reasoning,
                usage=RunUsage(
                    input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens, status=outcome.status
                ),
                error=error,
                result=result,
                finish_reason=progress.finish_reason,
            )
            await self._lifecycle.project(session, snapshot)
            if handler := self._agents.get(snapshot.resource_type):
                await handler.finish(session, snapshot, progress.agent_result)
            if outcome.points_enforced and snapshot.resource_type == 'chat' and snapshot.status == 'failed':
                await self._usage.waive_failed_chat(session, claim.id)
