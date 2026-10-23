"""The execution path for every admitted model call, including connection tests."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.developer.public import UsageService
from zhiheng.modules.models.public import ModelBinding, ModelProvider, ModelRequest, ProviderError
from zhiheng.runtime.contracts import ClaimedRun, RunError, RunStatus, RunUsage
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError

MAX_PROJECTION_CHARACTERS = 262144
PROJECTION_INTERVAL_SECONDS = 0.05
logger = logging.getLogger(__name__)


@dataclass
class InferenceProgress:
    text: str = ''
    input_tokens: int | None = None
    output_tokens: int | None = None
    provider_started: bool = False
    completion_seen: bool = False
    finish_reason: str | None = None
    result: dict[str, object] | None = None
    search_units: int | None = None
    cache_tokens: int | None = None
    reasoning_tokens: int | None = None


class InferenceExecutor:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        runs: RunStore,
        usage: UsageService,
        chat: ChatService,
        provider_factory: Callable[[ModelBinding], Awaitable[ModelProvider]],
        lease_seconds: int,
        timeout_seconds: float,
    ) -> None:
        self._sessions, self._runs, self._usage, self._chat = sessions, runs, usage, chat
        self._provider_factory, self._lease_seconds, self._timeout = provider_factory, lease_seconds, timeout_seconds

    async def execute(self, run_id: UUID) -> None:
        async with self._sessions.begin() as session:
            claim = await self._runs.claim(session, run_id, self._lease_seconds)
        if claim is None:
            return
        progress = InferenceProgress()
        consume = asyncio.create_task(self._consume(claim, progress))
        observe = asyncio.create_task(self._observe(claim))
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
            status, rejected = 'failed', exc.definitive_rejection
            error = RunError(code=exc.code, message=exc.message)
        except asyncio.CancelledError:
            consume.cancel()
            observe.cancel()
            await asyncio.gather(consume, observe, return_exceptions=True)
            await self._shutdown(claim, progress)
            raise
        except Exception as exc:
            logger.error(
                'Inference failed run_id=%s attempt_id=%s error_type=%s',
                claim.id,
                claim.attempt_id,
                type(exc).__name__,
                extra={'run_id': str(claim.id), 'attempt_id': str(claim.attempt_id), 'error_type': type(exc).__name__},
            )
            status = 'failed'
            error = RunError(code='inference_failed', message='模型调用未完成，请查看运行与用量记录')
        finally:
            consume.cancel()
            observe.cancel()
            await asyncio.gather(consume, observe, return_exceptions=True)
        try:
            await self._finish(claim, progress, status, error, rejected=rejected)
        except AppError as exc:
            if exc.code != 'execution_lost':
                raise
            logger.info(
                'Discarded a result after execution ownership expired run_id=%s attempt_id=%s',
                claim.id,
                claim.attempt_id,
                extra={'run_id': str(claim.id), 'attempt_id': str(claim.attempt_id)},
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
            await self._usage.require_reservation(session, claim.id, claim.owner_id, request.binding.model_id)
        provider = await self._provider_factory(request.binding)
        async with self._sessions.begin() as session:
            started = await self._runs.start_provider(session, claim.id, claim.attempt_id)
        if not started:
            raise AppError('execution_lost', '执行权已失效', 409)
        progress.provider_started = True
        last_projection = 0.0

        async def read_stream() -> None:
            nonlocal last_projection
            async for chunk in provider.stream(request.messages):
                progress.text += chunk.text
                if len(progress.text) > MAX_PROJECTION_CHARACTERS:
                    raise ProviderError
                if chunk.input_tokens is not None:
                    progress.input_tokens = chunk.input_tokens
                if chunk.output_tokens is not None:
                    progress.output_tokens = chunk.output_tokens
                if chunk.finish_reason is not None:
                    progress.completion_seen = True
                    progress.finish_reason = chunk.finish_reason
                if chunk.result is not None:
                    progress.result = chunk.result
                if chunk.search_units is not None:
                    progress.search_units = chunk.search_units
                if chunk.cache_tokens is not None:
                    progress.cache_tokens = chunk.cache_tokens
                if chunk.reasoning_tokens is not None:
                    progress.reasoning_tokens = chunk.reasoning_tokens
                if chunk.text and monotonic() - last_projection > PROJECTION_INTERVAL_SECONDS:
                    async with self._sessions.begin() as session:
                        if not await self._runs.project(session, claim.id, claim.attempt_id, progress.text):
                            raise AppError('execution_lost', '执行权已失效', 409)
                    last_projection = monotonic()
            if not progress.completion_seen:
                raise ProviderError

        await asyncio.wait_for(read_stream(), timeout=self._timeout)

    async def _observe(self, claim: ClaimedRun) -> str:
        while True:
            await asyncio.sleep(0.2)
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
                usage=RunUsage(
                    input_tokens=outcome.input_tokens, output_tokens=outcome.output_tokens, status=outcome.status
                ),
                error=error,
                result=progress.result,
                finish_reason=progress.finish_reason,
            )
            if snapshot.status == 'completed' and claim.resource_type == 'chat':
                await self._chat.complete(
                    session, conversation_id=claim.resource_id, run_id=claim.id, content=progress.text
                )
