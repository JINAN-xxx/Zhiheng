"""Worker-owned graph attempts run outside the ARQ slot that executes child Runs."""

import asyncio
import logging
import threading
from time import monotonic
from uuid import UUID

from zhiheng.application.knowledge.graph.runs import GraphModelCall, GraphRuns
from zhiheng.infrastructure.database import Database
from zhiheng.modules.billing.public import lock_billing_admission
from zhiheng.modules.knowledge.public import (
    GraphBuildRequest,
    GraphClaim,
    GraphJobInfo,
    GraphLabels,
    GraphOperation,
    GraphPort,
    GraphPreviewRequest,
    GraphQueryRequest,
    GraphSDKCall,
    GraphSDKOutcome,
    GraphStore,
    GraphSubgraph,
    GraphSubgraphRequest,
    GraphVectorManifest,
    VectorIOControl,
    validate_vectors,
)
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

logger = logging.getLogger(__name__)


class GraphJobs:
    def __init__(self, database: Database, store: GraphStore, runs: GraphRuns, sdk: GraphPort) -> None:
        self.database, self.store, self.runs, self.sdk = database, store, runs, sdk

    async def submit(
        self,
        principal: Principal,
        base_id: UUID,
        body: GraphBuildRequest | GraphPreviewRequest | GraphQueryRequest,
        operation: GraphOperation,
    ) -> GraphJobInfo:
        async with self.database.sessions.begin() as session:
            replay = await self.store.service.replay(session, principal, base_id, body, operation)
        if replay is not None:
            return replay
        if not await self.sdk.available():
            raise AppError('graph_unavailable', '未配置可用的图谱运行连接', 503)
        async with self.database.sessions.begin() as session:
            await lock_billing_admission(session)
            return await self.store.service.admit(session, principal, base_id, body, operation)

    async def execute(self, job_id: UUID) -> None:
        async with self.database.sessions.begin() as session:
            claim = await self.store.claim(session, job_id)
        if claim is None:
            return
        self.runs.start_attempt(claim)
        control = VectorIOControl(deadline=claim.deadline, lease_until=claim.lease_until)
        started = monotonic()
        writer_stopped = threading.Event()
        sdk_entered = threading.Event()
        work = asyncio.create_task(self._execute(claim, control, writer_stopped, sdk_entered))
        observer = asyncio.create_task(self._observe(claim, control))
        result = None
        status = 'failed'
        error: dict[str, object] | None = None
        try:
            done, _ = await asyncio.wait({work, observer}, return_when=asyncio.FIRST_COMPLETED)
            if observer in done:
                await observer
            outcome = await work
            result = outcome.result
            if claim.operation != 'query':
                result['seconds'] = monotonic() - started
            status = 'completed'
        except asyncio.CancelledError:
            status = 'interrupted'
            error = {'code': 'graph_worker_stopped', 'message': '图谱执行进程停止'}
        except Exception as exc:
            logger.error(
                'Graph execution failed job_id=%s attempt_id=%s error_type=%s',
                claim.job_id,
                claim.attempt_id,
                type(exc).__name__,
            )
            error = (
                {'code': exc.code, 'message': exc.message}
                if isinstance(exc, AppError)
                else {'code': 'graph_failed', 'message': '图谱构建或查询失败'}
            )
        finally:
            control.cancel()
            work.cancel()
            observer.cancel()

            cleanup = asyncio.create_task(
                self._drain_and_finish(
                    claim,
                    work,
                    observer,
                    writer_stopped,
                    sdk_entered,
                    status=status,
                    result=result,
                    error=error,
                ),
                name=f'graph-drain-{claim.attempt_id}',
            )
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:  # noqa: PERF203 - retain ownership through the terminal SQL commit
                    continue
            cleanup.result()

    async def _drain_and_finish(
        self,
        claim: GraphClaim,
        work: asyncio.Task[GraphSDKOutcome],
        observer: asyncio.Task[None],
        writer_stopped: threading.Event,
        sdk_entered: threading.Event,
        *,
        status: str,
        result: dict[str, object] | None,
        error: dict[str, object] | None,
    ) -> None:
        await asyncio.gather(work, observer, return_exceptions=True)
        await self.runs.drain(claim)
        self.runs.release(claim.attempt_id)
        # No SDK entry plus an actually terminal work task proves an unstarted
        # writer. Otherwise only the SDK's completed physical drain grants it.
        stopped = writer_stopped.is_set() or not sdk_entered.is_set()
        async with self.database.sessions.begin() as session:
            try:
                await self.store.finish(session, claim, status=status, result=result, error=error, stopped=stopped)
            except AppError as exc:
                await self.store.finish(
                    session,
                    claim,
                    status='failed',
                    result=None,
                    error={'code': exc.code, 'message': exc.message},
                    stopped=stopped,
                )

    async def _observe(self, claim: GraphClaim, control: VectorIOControl) -> None:
        while True:
            await asyncio.sleep(0.2)
            async with self.database.sessions.begin() as session:
                lease_until = await self.store.renew(session, claim)
            control.renew(lease_until)
            control.check_operation()

    async def _execute(
        self,
        claim: GraphClaim,
        control: VectorIOControl,
        writer_stopped: threading.Event,
        sdk_entered: threading.Event,
    ) -> GraphSDKOutcome:
        dimension = claim.frozen.dimension

        async def model(call: GraphSDKCall) -> object:
            snapshot = await self.runs.invoke(
                claim, GraphModelCall(call.role, call.operation, call.messages, call.output_tokens, call.json_object)
            )
            if call.role == 'embedding':
                return validate_vectors(snapshot.result, len(call.messages), dimension)
            if call.role == 'rerank':
                if snapshot.status == 'failed':
                    return {'_rerank_fallback': snapshot.error.code if snapshot.error else 'failed'}
                return snapshot.result
            return snapshot.text

        async with self.database.sessions.begin() as session:
            if claim.operation == 'query':
                manifest = GraphVectorManifest.load(await self.store.manifest(session, claim.revision))
            else:
                manifest = GraphVectorManifest(
                    claim.base_id,
                    claim.revision,
                    claim.attempt_id,
                    claim.frozen.fingerprint,
                    dimension,
                    claim.workspace,
                )
                await self.store.save_manifest(session, claim, manifest.dump())

        async def fence() -> None:
            async with self.database.sessions.begin() as session:
                await self.store.require_owned(session, claim)

        sdk_entered.set()
        return await self.sdk.execute(
            operation=claim.operation,
            frozen=claim.frozen,
            manifest=manifest,
            request=claim.request,
            model=model,
            control=control,
            fence=fence,
            stopped=writer_stopped.set,
        )

    async def _read(
        self, principal: Principal, base_id: UUID, operation: str, request: dict[str, object]
    ) -> GraphSDKOutcome:
        async with self.database.sessions.begin() as session:
            token, frozen, raw_manifest = await self.store.pin_read(session, principal, base_id)
            manifest = GraphVectorManifest.load(raw_manifest)

        async def no_model(call: GraphSDKCall) -> object:
            raise AppError('graph_unexpected_model', '图谱展示不能发起模型调用', 503)

        async def fence() -> None:
            async with self.database.sessions.begin() as session:
                await self.store.service.authorize(session, principal, base_id, frozen)

        try:
            outcome = await asyncio.wait_for(
                self.sdk.execute(
                    operation=operation,
                    frozen=frozen,
                    manifest=manifest,
                    request=request,
                    model=no_model,
                    control=VectorIOControl(timeout_seconds=8),
                    fence=fence,
                ),
                timeout=8,
            )
            await fence()
            return outcome
        except asyncio.TimeoutError:
            raise AppError('graph_read_timeout', '图谱读取超过执行期限', 503) from None
        finally:
            async with self.database.sessions.begin() as session:
                await self.store.release_pin(session, token)

    async def labels(self, principal: Principal, base_id: UUID) -> GraphLabels:
        return GraphLabels.model_validate((await self._read(principal, base_id, 'labels', {})).result)

    async def subgraph(self, principal: Principal, base_id: UUID, request: GraphSubgraphRequest) -> GraphSubgraph:
        return GraphSubgraph.model_validate(
            (await self._read(principal, base_id, 'subgraph', request.model_dump())).result
        )

    async def gc(self, revision: UUID) -> None:
        async with self.database.sessions.begin() as session:
            manifest = await self.store.claim_gc(session, revision)
        if manifest is None:
            return
        if manifest:
            try:
                await self.sdk.gc(GraphVectorManifest.load(manifest))
            except AppError as error:
                logger.warning('Graph GC deferred revision=%s code=%s', revision, error.code)
                async with self.database.sessions.begin() as session:
                    await self.store.defer_gc(session, revision)
                return
        async with self.database.sessions.begin() as session:
            await self.store.finish_gc(session, revision)
