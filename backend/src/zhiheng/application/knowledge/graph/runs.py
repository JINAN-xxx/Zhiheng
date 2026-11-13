"""Durable graph callbacks use the same Run, outbox, reservation and executor."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.infrastructure.database import Database
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.knowledge.public import (
    PER_BASE_CANDIDATES,
    GraphClaim,
    GraphModelOperation,
    GraphModelRole,
    GraphStore,
    fingerprint,
)
from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelOptions, ModelRequest, ModelService
from zhiheng.runtime.contracts import TERMINAL_STATUSES, ClaimedRun, RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


@dataclass(frozen=True)
class GraphModelCall:
    role: GraphModelRole
    operation: GraphModelOperation
    messages: tuple[ModelMessage, ...]
    output_tokens: int | None = None
    json_object: bool = False


class GraphRuns:
    def __init__(
        self,
        database: Database,
        store: GraphStore,
        models: ModelService,
        runs: RunStore,
        usage: UsageService,
        token_limit: int,
    ) -> None:
        self.database, self.store, self.models, self.runs, self.usage, self.token_limit = (
            database,
            store,
            models,
            runs,
            usage,
            token_limit,
        )
        self._semaphores: dict[UUID, asyncio.Semaphore] = {}
        self._active: dict[UUID, set[asyncio.Task[object]]] = {}
        self._accepting: set[UUID] = set()

    def start_attempt(self, claim: GraphClaim) -> None:
        self._accepting.add(claim.attempt_id)
        self._semaphores[claim.attempt_id] = asyncio.Semaphore(claim.frozen.config.concurrency)
        self._active[claim.attempt_id] = set()

    async def invoke(self, claim: GraphClaim, call: GraphModelCall) -> RunSnapshot:
        if claim.attempt_id not in self._accepting:
            raise AppError('graph_callback_closed', '图谱模型回调入口已停止', 409)
        task = asyncio.current_task()
        assert task is not None
        active = self._active.setdefault(claim.attempt_id, set())
        active.add(task)
        try:
            return await self._invoke(claim, call)
        finally:
            active.discard(task)
            if not active and claim.attempt_id in self._accepting:
                async with self.database.sessions.begin() as session:
                    await self.store.callback_returned(session, claim)

    @staticmethod
    def _binding(claim: GraphClaim, call: GraphModelCall) -> ModelBinding:
        if call.role == 'rerank':
            binding = ModelBinding.model_validate(claim.request.get('_rerank_binding'))
            if binding.model_type != 'rerank' or call.operation != 'rerank':
                raise AppError('graph_rerank_invalid', '图谱重排模型绑定无效', 409)
            top_n = claim.request.get('top_k', 8)
            if not isinstance(top_n, int):
                raise AppError('graph_rerank_invalid', '图谱重排数量无效', 409)
            binding = binding.model_copy(
                update={
                    'options': binding.options.model_copy(
                        update={
                            'top_n': min(8, top_n),
                            'return_documents': False,
                        }
                    )
                }
            )
        else:
            binding = ModelBinding.model_validate(
                claim.frozen.embedding_binding if call.role == 'embedding' else claim.frozen.llm_binding
            )
        if call.role not in {'embedding', 'rerank'}:
            maximum = min(
                binding.max_output_tokens,
                claim.frozen.config.max_output_tokens,
                call.output_tokens or claim.frozen.config.max_output_tokens,
            )
            if maximum < 1:
                raise AppError('invalid_graph_generation', '图谱输出上限无效', 422)
            binding = binding.model_copy(
                update={
                    'max_output_tokens': maximum,
                    'options': ModelOptions(response_format='json_object' if call.json_object else None),
                }
            )
        return binding

    async def _invoke(self, claim: GraphClaim, call: GraphModelCall) -> RunSnapshot:
        semaphore = self._semaphores.setdefault(claim.attempt_id, asyncio.Semaphore(claim.frozen.config.concurrency))
        async with semaphore:
            binding = self._binding(claim, call)
            request = ModelRequest(
                binding=binding,
                messages=call.messages,
                execution_timeout_seconds=5 if call.role == 'rerank' else min(600, claim.frozen.config.timeout_seconds),
                execution_deadline=claim.deadline,
            )
            digest = fingerprint(
                {
                    'request': request.model_dump(mode='json'),
                    'attempt': claim.attempt_id,
                    'role': call.role,
                    'operation': call.operation,
                }
            )
            async with self.database.sessions.begin() as session:
                await lock_billing_admission(session)
                ordinal = await self.store.next_step(session, claim)
                request_id = uuid5(claim.attempt_id, f'{call.role}:{call.operation}:{ordinal}')
                await self.runs.lock_request(session, claim.owner_id, request_id)
                run = await self.runs.create(
                    session,
                    owner_id=claim.owner_id,
                    billing_owner_id=claim.owner_id,
                    resource_type='knowledge_graph',
                    resource_id=claim.attempt_id,
                    request_id=request_id,
                    fingerprint=digest,
                    payload=request.model_dump(mode='json'),
                )
                rate = None
                with suppress(AppError):
                    rate = await self.models.latest_rate(session, binding.model_id)
                input_tokens = sum(
                    len(m.content.encode()) + 16 + sum(image.estimated_tokens for image in m.images)
                    for m in request.messages
                )
                output_tokens = binding.max_output_tokens if call.role not in {'embedding', 'rerank'} else 0
                await self.usage.reserve(
                    session,
                    user_id=claim.owner_id,
                    run_id=run.id,
                    model_id=binding.model_id,
                    source='knowledge_graph',
                    tokens=input_tokens + output_tokens,
                    estimated_input_tokens=input_tokens,
                    max_output_tokens=output_tokens,
                    token_limit=self.token_limit,
                    rate=rate,
                    operation='rerank'
                    if call.role == 'rerank'
                    else 'embedding'
                    if call.role == 'embedding'
                    else 'chat',
                    estimated_search_units=len(request.messages) - 1 if call.role == 'rerank' else 0,
                )
                await self.store.add_step(
                    session,
                    claim,
                    role=call.role,
                    operation=call.operation,
                    ordinal=ordinal,
                    run_id=run.id,
                    fingerprint=digest,
                )
            try:
                while True:
                    async with self.database.sessions.begin() as session:
                        await self.store.require_owned(session, claim)
                        snapshot = await self.runs.resource_snapshot(
                            session, run.id, 'knowledge_graph', claim.attempt_id
                        )
                    if snapshot.status in TERMINAL_STATUSES:
                        if snapshot.status != 'completed':
                            if (
                                call.role == 'rerank'
                                and snapshot.status == 'failed'
                                and snapshot.error is not None
                                and snapshot.error.code == 'knowledge_rerank_transient'
                            ):
                                raise AppError('knowledge_rerank_failed', '图谱重排服务暂时不可用', 502)
                            raise AppError('graph_model_failed', '图谱模型调用失败或结果不确定', 502)
                        if snapshot.finish_reason == 'length':
                            raise AppError('graph_model_truncated', '图谱抽取或摘要被输出上限截断', 422)
                        return snapshot
                    await asyncio.sleep(0.05)
            except BaseException:
                async with self.database.sessions.begin() as session:
                    _, release = await self.runs.resource_cancel(session, run.id, 'knowledge_graph', claim.attempt_id)
                    if release:
                        await self.usage.settle(session, run.id, rejected_before_execution=True)
                raise

    async def guard(self, claim: ClaimedRun) -> None:
        async with self.database.sessions.begin() as session:
            graph = await self.store.guard(session, claim.id, claim.resource_id)
            if claim.owner_id != graph.owner_id or (claim.billing_owner_id or claim.owner_id) != graph.owner_id:
                raise AppError('graph_execution_lost', '图谱步骤调用与计费归属无效', 409)
            request = ModelRequest.model_validate(claim.payload)
            if request.binding.model_type == 'rerank':
                expected = ModelBinding.model_validate(graph.request.get('_rerank_binding'))
                current = await self.models.resolve(session, expected.model_id)
                allowed = {chunk.content for chunk in graph.frozen.chunks}
                if (
                    current != expected
                    or request.binding != self._binding(graph, GraphModelCall('rerank', 'rerank', request.messages))
                    or not request.messages
                    or request.messages[0].content != graph.request.get('query')
                    or len(request.messages) - 1 > PER_BASE_CANDIDATES
                    or any(message.content not in allowed for message in request.messages[1:])
                ):
                    raise AppError('graph_rerank_stale', '图谱重排模型或来源已变化', 409)

    def release(self, attempt_id: UUID) -> None:
        self._accepting.discard(attempt_id)
        self._semaphores.pop(attempt_id, None)
        self._active.pop(attempt_id, None)

    async def drain(self, claim: GraphClaim) -> None:
        self._accepting.discard(claim.attempt_id)
        active = tuple(self._active.get(claim.attempt_id, set()))
        for task in active:
            task.cancel()
        await asyncio.gather(*active, return_exceptions=True)
        async with self.database.sessions() as session:
            ids = await self.store.child_runs(session, claim.attempt_id)
        for run_id in ids:
            async with self.database.sessions.begin() as session:
                _, release = await self.runs.resource_cancel(session, run_id, 'knowledge_graph', claim.attempt_id)
                if release:
                    await self.usage.settle(session, run_id, rejected_before_execution=True)
        while True:
            async with self.database.sessions() as session:
                snapshots = [
                    await self.runs.resource_snapshot(session, run_id, 'knowledge_graph', claim.attempt_id)
                    for run_id in ids
                ]
            if all(snapshot.status in TERMINAL_STATUSES for snapshot in snapshots):
                return
            await asyncio.sleep(0.05)

    async def authorize_read(self, session: AsyncSession, principal: Principal, snapshot: RunSnapshot) -> None:
        if snapshot.resource_type == 'knowledge_graph':
            await self.store.authorize_step_read(session, principal, snapshot.resource_id)
