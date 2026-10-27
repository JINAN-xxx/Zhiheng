"""Graph admission and full-source authorization owned by the knowledge domain."""

from datetime import timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.knowledge.graph.contracts import (
    GRAPH_TERMINAL,
    GraphBuildRequest,
    GraphChunk,
    GraphConfig,
    GraphConfigInfo,
    GraphConfigPatch,
    GraphDocumentSource,
    GraphFrozen,
    GraphJobInfo,
    GraphOperation,
    GraphPreviewRequest,
    GraphPreviewResult,
    GraphProgress,
    GraphQueryRequest,
    GraphQueryResult,
    GraphStatus,
    sdk_chunk_id,
)
from zhiheng.modules.knowledge.graph.orm import GraphAttempt, GraphConfiguration, GraphGeneration, GraphJob, GraphPin
from zhiheng.modules.knowledge.orm import IndexGeneration, KnowledgeChunk, KnowledgeDocument
from zhiheng.modules.knowledge.service import KnowledgeService, binding_fingerprint, fingerprint
from zhiheng.runtime.contracts import RunError
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


class GraphService:
    def __init__(self, knowledge: KnowledgeService) -> None:
        self.knowledge = knowledge

    async def config(
        self, session: AsyncSession, principal: Principal, base_id: UUID, *, available: bool
    ) -> GraphConfigInfo:
        base = await self.knowledge.require(session, principal, base_id)
        row = await session.get(GraphConfiguration, base_id)
        stale = False
        if row is not None and row.active_revision is not None:
            try:
                owner = await self.knowledge.identity.principal_for_user(session, base.owner_id)
                await self._active_generation(session, owner, base_id)
            except AppError:
                stale = True
        return GraphConfigInfo(
            config=GraphConfig.model_validate(row, from_attributes=True) if row else None,
            available=available,
            active_revision=row.active_revision if row else None,
            requires_rebuild=stale or bool(row and not row.active_revision),
            can_manage=self.knowledge.manages(principal, base),
            supported_reasoning_strategies=('auto', 'default'),
        )

    async def patch(self, session: AsyncSession, principal: Principal, base_id: UUID, body: GraphConfigPatch) -> None:
        await self.knowledge.require(session, principal, base_id, manage=True, lock=True)
        binding = await self.knowledge.models.resolve(session, body.llm_model_id)
        if binding.model_type != 'chat':
            raise AppError('graph_chat_required', '图谱抽取需要聊天模型', 422)
        if body.reasoning_strategy in {'low', 'disabled'}:
            # No model capability declaration exists for these policies yet.
            # Reject rather than infer vendor options from a model name.
            raise AppError('unsupported_capability', '该模型未声明所选推理参数能力', 422)
        row = await session.get(GraphConfiguration, base_id)
        actual = row.version if row else 0
        if body.expected_version != actual:
            raise AppError('graph_version_conflict', '图谱配置已更新，请刷新后重试', 409)
        fields = body.model_dump(exclude={'expected_version'})
        if row is None:
            session.add(GraphConfiguration(base_id=base_id, version=1, **fields))
        else:
            for name, value in fields.items():
                setattr(row, name, value)
            row.version += 1
            row.updated_at = utcnow()

    async def _freeze_source(
        self, session: AsyncSession, principal: Principal, owner: Principal, base_id: UUID, document: KnowledgeDocument
    ) -> GraphDocumentSource:
        if document.deleted_at is not None or document.active_revision is None or document.base_id != base_id:
            raise AppError('graph_source_stale', '图谱来源已变化', 409)
        for actor in (owner,) if owner.id == principal.id else (owner, principal):
            source = await self.knowledge.files.source_snapshot(
                session, actor, document.file_id, lock=True, artifact_file_id=document.artifact_file_id
            )
        if source.sha256 != document.source_sha256:
            raise AppError('graph_source_stale', '图谱来源已变化', 409)
        if document.artifact_file_id is None:
            raise AppError('graph_source_unparsed', '图谱需要已解析的受管来源', 409)
        original = await self.knowledge.files.require(session, owner, document.file_id, active=True, lock=True)
        artifact = await self.knowledge.files.require(session, owner, document.artifact_file_id, active=True, lock=True)
        if not artifact.parser_version or artifact.source_file_id != document.file_id:
            raise AppError('graph_source_unparsed', '图谱来源解析身份不完整', 409)
        return GraphDocumentSource(
            document_id=document.id,
            document_version=document.version,
            document_revision=document.active_revision,
            file_owner_id=original.owner_id,
            artifact_version=artifact.version,
            artifact_acl_version=artifact.acl_version,
            artifact_sha256=artifact.sha256,
            parser_version=artifact.parser_version,
            **source.model_dump(),
        )

    async def freeze(self, session: AsyncSession, principal: Principal, base_id: UUID) -> GraphFrozen:
        base = await self.knowledge.require(session, principal, base_id, lock=True)
        owner = await self.knowledge.identity.principal_for_user(session, base.owner_id)
        config_row = await session.get(GraphConfiguration, base_id)
        if config_row is None or base.active_revision is None:
            raise AppError('graph_not_configured', '请先配置图谱并完成向量索引', 409)
        config = GraphConfig.model_validate(config_row, from_attributes=True)
        bindings = await self.knowledge.models.resolve_many(session, [config.llm_model_id, base.embed_model_id])
        llm, embedding = bindings[config.llm_model_id], bindings[base.embed_model_id]
        if llm.model_type != 'chat' or embedding.model_type != 'embedding':
            raise AppError('unsupported_graph_models', '图谱需要聊天和Embedding模型的明确绑定', 422)
        generation = await session.get(IndexGeneration, base.active_revision)
        if generation is None or generation.status != 'active' or generation.base_id != base.id:
            raise AppError('graph_source_stale', '知识索引不可用，请重新构建', 409)
        if generation.fingerprint != binding_fingerprint(embedding):
            raise AppError('graph_source_stale', '知识索引模型已变化，请重新构建', 409)
        chunks = list(
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.generation_id == base.active_revision)
                .order_by(KnowledgeChunk.document_id, KnowledgeChunk.ordinal)
            )
        )
        if not chunks:
            raise AppError('graph_empty', '没有可用于图谱的受管分块', 409)
        documents = {
            row.id: row
            for row in await session.scalars(
                select(KnowledgeDocument)
                .where(KnowledgeDocument.id.in_({c.document_id for c in chunks}))
                .order_by(KnowledgeDocument.file_id, KnowledgeDocument.id)
            )
        }
        sources = [
            await self._freeze_source(session, principal, owner, base.id, document) for document in documents.values()
        ]
        if any(
            c.document_revision != documents[c.document_id].active_revision
            or c.file_id != documents[c.document_id].file_id
            for c in chunks
        ):
            raise AppError('graph_source_stale', '图谱分块版本与来源不匹配', 409)
        try:
            values = [
                GraphChunk(
                    id=c.id,
                    document_id=c.document_id,
                    file_id=c.file_id,
                    index_revision=c.generation_id,
                    document_revision=c.document_revision,
                    ordinal=c.ordinal,
                    content=c.content,
                    content_hash=c.content_hash,
                    sdk_id=sdk_chunk_id(c.document_id, c.id),
                )
                for c in chunks
            ]
        except ValueError:
            raise AppError('graph_source_invalid', '受管分块身份或内容指纹无效', 503) from None
        if generation.dimension is None:
            raise AppError('graph_source_stale', '知识索引维度不可用', 409)
        snapshot = GraphFrozen(
            base_id=base.id,
            base_owner_id=base.owner_id,
            config=config,
            llm_binding=llm.model_dump(mode='json'),
            embedding_binding=embedding.model_dump(mode='json'),
            index_revision=base.active_revision,
            dimension=generation.dimension,
            base_version=base.version,
            base_acl_version=base.acl_version,
            sources=sources,
            chunks=values,
            content_fingerprint='0' * 64,
            fingerprint='0' * 64,
        )
        snapshot = snapshot.model_copy(update={'content_fingerprint': self._content_fingerprint(snapshot)})
        return snapshot.model_copy(
            update={'fingerprint': fingerprint(snapshot.model_dump(mode='json', exclude={'fingerprint'}))}
        )

    @staticmethod
    def _content_fingerprint(frozen: GraphFrozen) -> str:
        payload = frozen.model_dump(
            mode='json', exclude={'fingerprint', 'content_fingerprint', 'base_version', 'base_acl_version'}
        )
        payload['sources'] = [
            source.model_dump(
                mode='json',
                include={
                    'document_id',
                    'document_revision',
                    'file_id',
                    'sha256',
                    'artifact_file_id',
                    'artifact_sha256',
                    'parser_version',
                },
            )
            for source in frozen.sources
        ]
        return fingerprint(payload)

    @staticmethod
    def validate_frozen(frozen: GraphFrozen) -> None:
        payload = frozen.model_dump(mode='json', exclude={'fingerprint'})
        if (
            fingerprint(payload) != frozen.fingerprint
            or GraphService._content_fingerprint(frozen) != frozen.content_fingerprint
        ):
            raise AppError('graph_snapshot_corrupt', '图谱快照指纹不匹配', 503)

    @staticmethod
    def _validate_generation(generation: GraphGeneration) -> None:
        frozen = GraphFrozen.model_validate(generation.frozen)
        GraphService.validate_frozen(frozen)
        expected = {
            'base_id': str(generation.base_id),
            'revision': str(generation.id),
            'attempt_id': str(generation.attempt_id),
            'fingerprint': frozen.fingerprint,
            'dimension': frozen.dimension,
            'workspace': 'g_' + generation.attempt_id.hex,
        }
        if generation.fingerprint != frozen.fingerprint or generation.manifest != expected:
            raise AppError('graph_manifest_invalid', '图谱版本完整身份不匹配', 503)

    async def authorize(self, session: AsyncSession, principal: Principal, base_id: UUID, frozen: GraphFrozen) -> None:
        self.validate_frozen(frozen)
        current = await self.freeze(session, principal, base_id)
        if current.content_fingerprint != frozen.content_fingerprint:
            raise AppError('graph_stale', '图谱来源、模型或配置已变化，请重新构建', 409)

    async def _active_generation(self, session: AsyncSession, principal: Principal, base_id: UUID) -> GraphGeneration:
        await self.knowledge.require(session, principal, base_id)
        config = await session.get(GraphConfiguration, base_id)
        generation = (
            await session.get(GraphGeneration, config.active_revision) if config and config.active_revision else None
        )
        if generation is None or generation.status != 'active':
            raise AppError('graph_not_ready', '图谱尚未完成构建', 409)
        self._validate_generation(generation)
        await self.authorize(session, principal, base_id, GraphFrozen.model_validate(generation.frozen))
        return generation

    async def replay(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        body: GraphBuildRequest | GraphPreviewRequest | GraphQueryRequest,
        operation: GraphOperation,
    ) -> GraphJobInfo | None:
        await self.knowledge.require(session, principal, base_id)
        digest = fingerprint({'base_id': base_id, 'operation': operation, 'request': body.model_dump(mode='json')})
        row = await session.scalar(
            select(GraphJob).where(
                GraphJob.actor_id == principal.id, GraphJob.client_request_id == body.client_request_id
            )
        )
        if row is None:
            return None
        if row.base_id != base_id or row.request_fingerprint != digest:
            raise AppError('idempotency_conflict', '请求标识已用于其他内容', 409)
        return await self.job(session, principal, base_id, row.id)

    async def reuse_active(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        owner_id: UUID,
        body: GraphBuildRequest,
        frozen: GraphFrozen,
        digest: str,
    ) -> GraphJobInfo | None:
        if body.rebuild:
            return None
        config = await session.get(GraphConfiguration, base_id)
        generation = (
            await session.get(GraphGeneration, config.active_revision) if config and config.active_revision else None
        )
        if generation is None or generation.status != 'active':
            return None
        previous_frozen = GraphFrozen.model_validate(generation.frozen)
        if previous_frozen.content_fingerprint != frozen.content_fingerprint:
            return None
        self._validate_generation(generation)
        previous = await session.scalar(
            select(GraphJob)
            .where(
                GraphJob.graph_revision == generation.id, GraphJob.operation == 'build', GraphJob.status == 'completed'
            )
            .order_by(GraphJob.created_at.desc())
            .limit(1)
        )
        if previous is None or previous.result is None:
            return None
        result = GraphPreviewResult.model_validate(previous.result).model_copy(update={'seconds': 0.0})
        job_id, attempt_id = uuid4(), uuid4()
        now = utcnow()
        job = GraphJob(
            id=job_id,
            base_id=base_id,
            actor_id=principal.id,
            owner_id=owner_id,
            client_request_id=body.client_request_id,
            request_fingerprint=digest,
            operation='build',
            status='completed',
            phase='reused',
            attempt_id=attempt_id,
            graph_revision=generation.id,
            frozen=frozen.model_dump(mode='json'),
            request=body.model_dump(mode='json'),
            total_documents=len(frozen.sources),
            processed_documents=len(frozen.sources),
            progress=GraphProgress(
                extracted_chunks=result.chunks,
                total_chunks=result.chunks,
                entities=result.entities,
                relations=result.relations,
            ).model_dump(),
            result=result.model_dump(),
            created_at=now,
            updated_at=now,
            finished_at=now,
        )
        session.add(job)
        await session.flush()
        session.add(
            GraphAttempt(
                id=attempt_id,
                job_id=job_id,
                status='completed',
                workspace='g_' + attempt_id.hex,
                deadline=now,
                writer_stopped=True,
                stopped_at=now,
            )
        )
        await session.flush()
        return self._info(job)

    @staticmethod
    def _selection_counts(
        body: GraphBuildRequest | GraphPreviewRequest | GraphQueryRequest,
        frozen: GraphFrozen,
        operation: GraphOperation,
    ) -> tuple[int | None, int | None]:
        selected = list(frozen.chunks)
        if isinstance(body, GraphPreviewRequest):
            if body.document_id is not None:
                selected = [chunk for chunk in selected if chunk.document_id == body.document_id]
            selected = selected[:3]
            if not selected:
                raise AppError('graph_preview_empty', '没有可用于试构建的受管分块', 404)
        total_documents = len({chunk.document_id for chunk in selected}) if operation != 'query' else None
        total_chunks = len(selected) if operation != 'query' else None
        return total_documents, total_chunks

    async def admit(
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        body: GraphBuildRequest | GraphPreviewRequest | GraphQueryRequest,
        operation: GraphOperation,
    ) -> GraphJobInfo:
        base = await self.knowledge.require(
            session, principal, base_id, manage=operation in {'build', 'preview'}, lock=True
        )
        if operation in {'build', 'preview'}:
            self.knowledge.idle(base)
        key = int(fingerprint({'actor': principal.id, 'request': body.client_request_id, 'kind': 'graph'})[:16], 16) - (
            1 << 63
        )
        await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
        digest = fingerprint({'base_id': base_id, 'operation': operation, 'request': body.model_dump(mode='json')})
        existing = await session.scalar(
            select(GraphJob).where(
                GraphJob.actor_id == principal.id, GraphJob.client_request_id == body.client_request_id
            )
        )
        if existing is not None:
            if existing.request_fingerprint != digest or existing.base_id != base_id:
                raise AppError('idempotency_conflict', '请求标识已用于其他内容', 409)
            return await self.job(session, principal, base_id, existing.id)
        frozen = await self.freeze(session, principal, base_id)
        revision = uuid4()
        if isinstance(body, GraphQueryRequest):
            generation = await self._active_generation(session, principal, base_id)
            revision = generation.id
        else:
            if frozen.config.version != body.expected_version:
                raise AppError('graph_version_conflict', '图谱配置已更新', 409)
            if await session.scalar(
                select(GraphJob.id)
                .where(
                    GraphJob.base_id == base_id,
                    GraphJob.operation.in_(('build', 'preview')),
                    GraphJob.status.not_in(GRAPH_TERMINAL),
                )
                .limit(1)
            ):
                raise AppError('graph_busy', '图谱仍有执行中的任务', 409)
        if isinstance(body, GraphBuildRequest):
            reused = await self.reuse_active(session, principal, base_id, base.owner_id, body, frozen, digest)
            if reused is not None:
                return reused
        total_documents, total_chunks = self._selection_counts(body, frozen, operation)
        attempt_id, job_id = uuid4(), uuid4()
        job = GraphJob(
            id=job_id,
            base_id=base_id,
            actor_id=principal.id,
            owner_id=principal.id if operation == 'query' else base.owner_id,
            client_request_id=body.client_request_id,
            request_fingerprint=digest,
            operation=operation,
            attempt_id=attempt_id,
            graph_revision=revision,
            frozen=frozen.model_dump(mode='json'),
            request=body.model_dump(mode='json'),
            total_documents=total_documents,
            processed_documents=0 if operation != 'query' else None,
            progress=GraphProgress(total_chunks=total_chunks).model_dump(),
        )
        session.add(job)
        await session.flush()
        session.add(
            GraphAttempt(
                id=attempt_id,
                job_id=job_id,
                workspace='g_' + attempt_id.hex,
                deadline=utcnow() + timedelta(seconds=frozen.config.timeout_seconds),
            )
        )
        await session.flush()
        if operation == 'query':
            session.add(
                GraphPin(
                    generation_id=revision,
                    job_id=job_id,
                    owner_token=attempt_id,
                    lease_until=utcnow() + timedelta(seconds=frozen.config.timeout_seconds + 10),
                )
            )
        if operation != 'query':
            session.add(
                GraphGeneration(
                    id=revision,
                    base_id=base_id,
                    attempt_id=attempt_id,
                    fingerprint=frozen.fingerprint,
                    manifest={},
                    frozen=frozen.model_dump(mode='json'),
                )
            )
        await session.flush()
        return self._info(job)

    async def authorize_history(
        self, session: AsyncSession, principal: Principal, base_id: UUID, frozen: GraphFrozen
    ) -> None:
        self.validate_frozen(frozen)
        await self.knowledge.require(session, principal, base_id)
        if frozen.base_id != base_id:
            raise AppError('graph_snapshot_corrupt', '图谱作业归属不匹配', 503)
        owner = await self.knowledge.identity.principal_for_user(session, frozen.base_owner_id)
        for source in frozen.sources:
            for actor in (owner,) if owner.id == principal.id else (owner, principal):
                current = await self.knowledge.files.source_snapshot(
                    session, actor, source.file_id, lock=True, artifact_file_id=source.artifact_file_id
                )
                if current.sha256 != source.sha256:
                    raise AppError('graph_source_changed', '历史图谱来源内容已变化', 409)
            artifact = await self.knowledge.files.require(session, principal, source.artifact_file_id, active=True)
            if artifact.sha256 != source.artifact_sha256 or artifact.parser_version != source.parser_version:
                raise AppError('graph_source_changed', '历史图谱解析来源已变化', 409)

    async def job(self, session: AsyncSession, principal: Principal, base_id: UUID, job_id: UUID) -> GraphJobInfo:
        await self.knowledge.require(session, principal, base_id)
        row = await session.get(GraphJob, job_id)
        if row is None or row.base_id != base_id or (row.operation == 'query' and row.actor_id != principal.id):
            raise AppError('graph_job_not_found', '图谱作业不存在', 404)
        await self.authorize_history(session, principal, base_id, GraphFrozen.model_validate(row.frozen))
        return self._info(row)

    async def jobs(self, session: AsyncSession, principal: Principal, base_id: UUID) -> list[GraphJobInfo]:
        await self.knowledge.require(session, principal, base_id)
        rows = list(
            await session.scalars(
                select(GraphJob)
                .where(
                    GraphJob.base_id == base_id, or_(GraphJob.operation != 'query', GraphJob.actor_id == principal.id)
                )
                .order_by(GraphJob.created_at.desc())
                .limit(50)
            )
        )
        result = []
        for row in rows:
            try:
                await self.authorize_history(session, principal, base_id, GraphFrozen.model_validate(row.frozen))
            except AppError as error:
                if error.status_code in {403, 404, 409}:
                    continue
                raise
            result.append(self._info(row))
        return result

    async def cancel(self, session: AsyncSession, principal: Principal, base_id: UUID, job_id: UUID) -> GraphJobInfo:
        # Match worker renewal: base -> job -> attempt -> current source ACL.
        # History cancellation also permits queued/terminal attempts, so it
        # deliberately does not use the worker's running lease/token guard.
        await self.knowledge.require(session, principal, base_id, lock=True)
        row = await session.scalar(
            select(GraphJob)
            .where(
                GraphJob.id == job_id,
                GraphJob.base_id == base_id,
                or_(GraphJob.operation != 'query', GraphJob.actor_id == principal.id),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AppError('graph_job_not_found', '图谱作业不存在', 404)
        attempt = await session.scalar(
            select(GraphAttempt)
            .where(GraphAttempt.id == row.attempt_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        await self.authorize_history(session, principal, base_id, GraphFrozen.model_validate(row.frozen))
        if row.actor_id != principal.id:
            await self.knowledge.require(session, principal, base_id, manage=True)
        if row.status == 'pending' and attempt is not None and attempt.owner_token is None:
            attempt.status = 'cancelled'
            attempt.writer_stopped = True
            attempt.stopped_at = utcnow()
            row.status = 'cancelled'
            row.phase = 'finished'
            row.finished_at = utcnow()
            row.updated_at = utcnow()
            row.version += 1
            row.error = {'code': 'graph_cancelled', 'message': '图谱作业已取消'}
            row.cancel_requested_at = utcnow()
            if row.operation != 'query' and row.graph_revision is not None:
                generation = await session.get(GraphGeneration, row.graph_revision)
                if generation is not None:
                    generation.status = 'retired'
                    generation.gc_after = utcnow()
            await session.execute(delete(GraphPin).where(GraphPin.job_id == row.id))
        if row.status not in GRAPH_TERMINAL:
            row.cancel_requested_at = row.cancel_requested_at or utcnow()
            row.status = 'draining'
            row.version += 1
            row.updated_at = utcnow()
        return self._info(row)

    @staticmethod
    def _info(row: GraphJob) -> GraphJobInfo:
        result = None
        if row.result is not None:
            result = (
                GraphQueryResult.model_validate(row.result)
                if row.operation == 'query'
                else GraphPreviewResult.model_validate(row.result)
            )
        return GraphJobInfo(
            id=row.id,
            base_id=row.base_id,
            operation=cast('GraphOperation', row.operation),
            status=cast('GraphStatus', row.status),
            phase=row.phase,
            version=row.version,
            attempt_id=row.attempt_id,
            graph_revision=row.graph_revision,
            processed_documents=row.processed_documents,
            total_documents=row.total_documents,
            progress=GraphProgress.model_validate(row.progress),
            error=RunError.model_validate(row.error) if row.error else None,
            result=result,
            cancel_allowed=row.status not in GRAPH_TERMINAL,
            created_at=row.created_at,
            updated_at=row.updated_at,
            finished_at=row.finished_at,
        )
