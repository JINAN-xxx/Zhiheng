"""Finite knowledge ticks submit model children and always return their queue slot."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.files.inputs import FileInputService
from zhiheng.infrastructure.database import Database
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.developer.public import APIOrigin
from zhiheng.modules.files.public import FileService
from zhiheng.modules.knowledge.public import (
    JOB_TERMINAL,
    BuildClaim,
    BuildStore,
    GenerationStore,
    KnowledgeService,
    ParsingPending,
    VectorIOControl,
    VectorPort,
    binding_fingerprint,
    fingerprint,
    split_chunks,
    validate_vectors,
)
from zhiheng.modules.models.public import ModelMessage, ModelRequest, ModelService
from zhiheng.runtime.contracts import TERMINAL_STATUSES
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from zhiheng.application.files.parsing import DocumentParsing
    from zhiheng.application.knowledge.origin import KnowledgeOriginGuard


class KnowledgeJobs:
    def __init__(
        self,
        database: Database,
        knowledge: KnowledgeService,
        file_inputs: FileInputService,
        files: FileService,
        models: ModelService,
        runs: RunStore,
        usage: UsageService,
        vectors: VectorPort,
        token_limit: int,
    ) -> None:
        self.database, self.knowledge, self.file_inputs, self.files = database, knowledge, file_inputs, files
        self.models, self.runs, self.usage, self.vectors = models, runs, usage, vectors
        self.store, self.token_limit = BuildStore(knowledge), token_limit
        self.generations = GenerationStore()
        self.origin_guard: KnowledgeOriginGuard | None = None
        self.document_parsing: DocumentParsing | None = None

    async def authorize_parse_origin(self, job_id: UUID) -> None:
        if self.origin_guard is not None:
            async with self.database.sessions.begin() as session:
                await self.origin_guard.job(session, job_id)

    async def authorize_origin(
        self, session: AsyncSession, claim: BuildClaim, *, models: bool = True
    ) -> APIOrigin | None:
        await self.knowledge.validate_migration(session, claim.id)
        if self.origin_guard is None:
            return None
        paid = models and not claim.operation.startswith('delete')
        origin = await self.origin_guard.job(session, claim.id, model_ids=(claim.binding.model_id,) if paid else ())
        if origin is not None and paid:
            binding = await self.models.resolve(session, claim.binding.model_id)
            if binding_fingerprint(binding) != binding_fingerprint(claim.binding):
                raise AppError('knowledge_preparation_stale', '知识模型绑定已变化', 409)
        if origin is not None:
            principal = await self.knowledge.identity.principal_for_user(session, origin.owner_id)
            await self.knowledge.authorize_base(
                session,
                principal,
                claim.base_id,
                manage=True,
                lock=True,
                include_deleted=claim.operation == 'delete_base',
            )
            await self.store.owned(session, claim)
            await self.origin_guard.job(
                session, claim.id, model_ids=(claim.binding.model_id,) if paid else (), lock=True
            )
        return origin

    async def tick(self, job_id: UUID) -> None:
        try:
            async with self.database.sessions.begin() as session:
                facts = await self.store.facts(session, job_id)
                if (
                    self.origin_guard is not None
                    and facts is not None
                    and facts.status not in {*JOB_TERMINAL, 'cancel_requested'}
                ):
                    paid = not facts.operation.startswith('delete')
                    origin = await self.origin_guard.job(
                        session, job_id, model_ids=(facts.binding.model_id,) if paid else ()
                    )
                    if origin is not None and paid:
                        binding = await self.models.resolve(session, facts.binding.model_id)
                        if binding_fingerprint(binding) != binding_fingerprint(facts.binding):
                            raise AppError('knowledge_preparation_stale', '知识模型绑定已变化', 409)
                claim = await self.store.claim(session, job_id)
        except AppError as error:
            async with self.database.sessions.begin() as session:
                await self.store.reject(session, job_id, error)
            await self.cancel_children(job_id)
            return
        if claim is None:
            await self.cancel_children(job_id)
            return
        claim = replace(
            claim,
            io_control=VectorIOControl(
                deadline=claim.started_at + timedelta(seconds=60),
                lease_until=claim.lease_until,
            ),
        )
        assert claim.io_control is not None
        execution = asyncio.create_task(self._stage(claim))
        control = asyncio.create_task(self._observe(claim))
        try:
            done, _ = await asyncio.wait({execution, control}, return_when=asyncio.FIRST_COMPLETED)
            if control in done:
                claim.io_control.cancel()
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                if control.result() == 'cancel':
                    raise AppError('knowledge_cancelled', '知识任务已取消', 409)
                return
            await execution
        except asyncio.CancelledError:
            claim.io_control.cancel()
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            async with self.database.sessions.begin() as session:
                await self.store.fail_claim(session, claim, 'interrupted', 'worker_stopped', '知识执行进程停止')
            raise
        except Exception as error:
            code, message = (
                (error.code, error.message)
                if isinstance(error, AppError)
                else ('knowledge_build_failed', '知识索引构建失败')
            )
            async with self.database.sessions.begin() as session:
                await self.store.fail_claim(session, claim, 'failed', code, message)
        finally:
            claim.io_control.cancel()
            control.cancel()
            await asyncio.gather(control, return_exceptions=True)
        await self.cancel_children(job_id)

    async def cancel_children(self, job_id: UUID) -> None:
        async with self.database.sessions() as session:
            outcome = await self.store.interrupted_children(session, job_id)
        if outcome is None:
            return
        owner_id, children = outcome
        for run_id in children:
            async with self.database.sessions.begin() as session:
                _, release = await self.runs.cancel(session, run_id, owner_id)
                if release:
                    await self.usage.settle(session, run_id, rejected_before_execution=True)

    async def gc(self, generation_id: UUID) -> None:
        async with self.database.sessions.begin() as session:
            claimed = await self.generations.claim(session, generation_id)
        if claimed is None:
            return
        spec, created = claimed
        if created:
            await self.vectors.drop(spec)
        async with self.database.sessions.begin() as session:
            await self.generations.finish(session, generation_id)

    async def _observe(self, claim: BuildClaim) -> str:
        while True:
            await asyncio.sleep(0.2)
            async with self.database.sessions.begin() as session:
                await self.authorize_origin(session, claim)
                state, lease_until = await self.store.control(session, claim)
            if state != 'active':
                return state
            if claim.io_control is not None and lease_until is not None:
                claim.io_control.renew(lease_until)

    async def _stage(self, claim: BuildClaim) -> None:
        async with self.database.sessions.begin() as session:
            await self.authorize_origin(session, claim)
        handlers = {'parse': self._parse, 'embedding': self._embedding, 'write': self._write, 'publish': self._publish}
        if claim.phase not in handlers:
            raise AppError('invalid_build_phase', '知识任务阶段无效', 503)
        await handlers[claim.phase](claim)

    async def _parse(self, claim: BuildClaim) -> None:
        entry = next((row for row in claim.documents if row['status'] == 'pending'), None)
        if entry is None:
            async with self.database.sessions.begin() as session:
                await self.store.release(session, claim, 'embedding')
            return
        document_id = UUID(str(entry['document_id']))
        try:
            async with self.database.sessions.begin() as session:
                owner = await self.knowledge.identity.principal_for_user(session, claim.owner_id)
                file_id, size, overlap = await self.store.source(session, claim, document_id)
            attachment = (
                await self.document_parsing.prepare(owner, file_id, claim.id)
                if self.document_parsing is not None
                else await self.file_inputs.prepare(owner, file_id)
            )
            if attachment.image_data is not None:
                raise AppError('ocr_required', '图像入库需要OCR解析', 422)
            chunks = split_chunks(attachment.text, size, overlap)
            async with self.database.sessions.begin() as session:
                await self.authorize_origin(session, claim)
                source = await self.files.source_snapshot(
                    session, owner, file_id, artifact_file_id=attachment.summary.artifact_file_id
                )
                await self.store.parsed(session, claim, document_id, chunks, source)
        except ParsingPending:
            async with self.database.sessions.begin() as session:
                await self.store.release(session, claim, 'parse', 'waiting_parse')
            return
        except (AppError, ValueError) as error:
            safe = error if isinstance(error, AppError) else AppError('invalid_chunks', '知识分块无效或超过2000块', 422)
            async with self.database.sessions.begin() as session:
                await self.store.document_failed(session, claim, document_id, safe)
        async with self.database.sessions.begin() as session:
            await self.store.release(session, claim, 'parse')
        return

    async def _embedding(self, claim: BuildClaim) -> None:
        async with self.database.sessions.begin() as session:
            batch = await self.store.next_batch(session, claim)
            if batch is None:
                await self.store.release(session, claim, 'write')
                return
            child_id = await self.store.step(session, claim.id, batch.key)
        if child_id is None:
            async with self.database.sessions.begin() as session:
                await lock_billing_admission(session)
                origin = await self.authorize_origin(session, claim, models=True)
                owner = await self.knowledge.identity.principal_for_user(session, claim.owner_id)
                await self.models.resolve(session, claim.binding.model_id)
                await self.store.owned(session, claim)
                request = ModelRequest(
                    binding=claim.binding,
                    messages=tuple(ModelMessage('user', text) for text in batch.texts),
                )
                value = fingerprint(request.model_dump(mode='json'))
                request_id = uuid5(claim.id, batch.key)
                await self.runs.lock_request(session, owner.id, request_id)
                run = await self.runs.existing_request(session, owner.id, request_id, value)
                if run is None:
                    run = await self.runs.create(
                        session,
                        owner_id=owner.id,
                        billing_owner_id=owner.id,
                        resource_type='knowledge_embedding',
                        resource_id=claim.id,
                        request_id=request_id,
                        fingerprint=value,
                        payload=request.model_dump(mode='json'),
                    )
                    rate = await self.usage.money_rate(
                        session, self.models, claim.binding.model_id, required=origin is not None
                    )
                    await self.usage.reserve(
                        session,
                        user_id=owner.id,
                        run_id=run.id,
                        model_id=claim.binding.model_id,
                        source='knowledge_embedding',
                        tokens=sum(len(text.encode()) + 16 for text in batch.texts),
                        estimated_input_tokens=sum(len(text.encode()) + 16 for text in batch.texts),
                        max_output_tokens=0,
                        token_limit=self.token_limit,
                        key_id=origin.key_id if origin else None,
                        rate=rate,
                        operation='embedding',
                    )
                await self.store.add_step(session, claim.id, batch.key, run.id, value)
                await self.store.release(session, claim, 'embedding', 'waiting_model')
            return
        async with self.database.sessions() as session:
            run = await self.runs.resource_snapshot(session, child_id, 'knowledge_embedding', claim.id)
        if run.status not in TERMINAL_STATUSES:
            async with self.database.sessions.begin() as session:
                await self.store.release(session, claim, 'embedding', 'waiting_model')
            return
        try:
            if run.status != 'completed':
                raise AppError(
                    run.error.code if run.error else 'embedding_failed',
                    run.error.message if run.error else 'Embedding调用未完成，请查看运行与用量记录后重试处理',
                    502,
                )
            vectors = validate_vectors(run.result, len(batch.texts), batch.dimension)
            async with self.database.sessions.begin() as session:
                await self.store.vectors(session, claim, batch, vectors)
        except (AppError, ValueError) as error:
            safe = error if isinstance(error, AppError) else AppError('invalid_embedding', 'Embedding结果无效', 502)
            async with self.database.sessions.begin() as session:
                await self.store.document_failed(session, claim, batch.document_id, safe)
        async with self.database.sessions.begin() as session:
            await self.store.release(session, claim, 'embedding')
        return

    async def _write(self, claim: BuildClaim) -> None:
        async with self.database.sessions.begin() as session:
            spec, rows = await self.store.candidate(session, claim)
        if rows:
            await self.vectors.build(spec, rows, control=claim.io_control)
        async with self.database.sessions.begin() as session:
            await self.authorize_origin(session, claim)
            await self.store.release(session, claim, 'publish', 'publishing')

    async def _publish(self, claim: BuildClaim) -> None:
        async with self.database.sessions.begin() as session:
            await self.authorize_origin(session, claim, models=not claim.operation.startswith('delete'))
            binding = (
                claim.binding
                if claim.operation.startswith('delete')
                else await self.models.resolve(session, claim.binding.model_id)
            )
            await self.knowledge.validate_migration(session, claim.id, lock=True)
            await self.store.publish(session, claim, binding)
            await self.knowledge.finish_migration(session, claim.id)
