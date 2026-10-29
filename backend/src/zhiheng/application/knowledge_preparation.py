"""Durable, paid-call-idempotent retrieval before final model admission."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.lifecycle import RunLifecycle
from zhiheng.infrastructure.database import Database
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.developer.public import ApplicationService, DeveloperIdentity, KeyService
from zhiheng.modules.knowledge.public import (
    BuildStore,
    Hit,
    KnowledgeService,
    MilvusStore,
    PreparationInfo,
    PreparedKnowledge,
    QueryRequest,
    RetrievalAdmission,
    RetrievalBase,
    RetrievalState,
    RetrievalStore,
    binding_fingerprint,
    fingerprint,
    validate_vectors,
)
from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelRequest, ModelService
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

MAX_HITS_BYTES = 24000
MAX_QUERY_CHARACTERS = 8000


@dataclass(frozen=True)
class PreparationStart:
    owner_id: UUID
    resource_type: str
    resource_id: UUID
    request_id: UUID
    value: str
    request: dict[str, object]
    base_ids: list[UUID]
    dependencies: dict[str, object]
    namespace_id: UUID | None = None


class KnowledgePreparation:
    def __init__(
        self,
        database: Database,
        knowledge: KnowledgeService,
        models: ModelService,
        runs: RunStore,
        usage: UsageService,
        keys: KeyService,
        applications: ApplicationService,
        vectors: MilvusStore,
        token_limit: int,
    ) -> None:
        self.database, self.knowledge, self.models, self.runs = database, knowledge, models, runs
        self.usage, self.keys, self.vectors, self.token_limit = usage, keys, vectors, token_limit
        self.applications = applications
        self.store, self.steps = RetrievalStore(knowledge), BuildStore(knowledge)
        self.finalizers: dict[
            str, Callable[[AsyncSession, RetrievalState, PreparedKnowledge], Awaitable[RunSnapshot]]
        ] = {}
        self.terminal_callbacks: dict[str, Callable[[AsyncSession, RetrievalState], Awaitable[None]]] = {}
        self.dependency_guards: dict[str, Callable[[AsyncSession, RetrievalState], Awaitable[None]]] = {}

    async def binding_for(
        self, session: AsyncSession, owner: Principal, model_id: UUID, base_ids: list[UUID]
    ) -> ModelBinding:
        owner = await self.knowledge.identity.principal_for_user(session, owner.id)
        bases = await self.store.snapshot(session, owner, base_ids)
        bindings = await self.models.resolve_many(
            session, sorted({model_id, *(base.binding.model_id for base in bases)})
        )
        for base in bases:
            if binding_fingerprint(bindings[base.binding.model_id]) != base.spec.fingerprint:
                raise AppError('knowledge_reindex_required', 'Embedding绑定已变化，请显式重建索引', 409)
        return bindings[model_id]

    async def can_use(self, session: AsyncSession, owner_id: UUID, base_ids: list[UUID]) -> bool:
        try:
            owner = await self.knowledge.identity.principal_for_user(session, owner_id)
            bases = await self.store.snapshot(session, owner, base_ids)
            for base in bases:
                current = await self.models.resolve(session, base.binding.model_id, lock=False)
                if binding_fingerprint(current) != base.spec.fingerprint:
                    return False
        except AppError:
            return False
        return True

    async def start(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        body: PreparationStart,
        identity: DeveloperIdentity | None = None,
    ) -> PreparationInfo:
        await lock_billing_admission(session)
        owner_id, resource_type, resource_id = body.owner_id, body.resource_type, body.resource_id
        request_id, value, request = body.request_id, body.value, body.request
        base_ids, dependencies, namespace_id = body.base_ids, body.dependencies, body.namespace_id
        namespace = namespace_id or principal.id
        query = str(request.get('query', ''))
        if not 1 <= len(query) <= MAX_QUERY_CHARACTERS or '\x00' in query:
            raise AppError('invalid_knowledge_query', '知识检索问题须为1到8000字符', 422)
        await self.runs.lock_request(session, namespace, request_id)
        existing = await self.store.existing(session, namespace, request_id, value)
        if existing:
            return self.store.info(existing)
        owner = await self.knowledge.identity.principal_for_user(session, owner_id)
        bases = await self.store.snapshot(session, owner, base_ids)
        bindings = await self.models.resolve_many(session, sorted({base.binding.model_id for base in bases}))
        for base in bases:
            current = bindings[base.binding.model_id]
            if binding_fingerprint(current) != base.spec.fingerprint:
                raise AppError('knowledge_reindex_required', 'Embedding绑定已变化，请显式重建索引', 409)
        if identity:
            await self.keys.authorize_identity(session, identity, [base.binding.model_id for base in bases], lock=True)
        row = await self.store.create(
            session,
            body=RetrievalAdmission(
                namespace_id=namespace,
                request_id=request_id,
                actor_id=principal.id,
                owner_id=owner_id,
                resource_type=resource_type,
                resource_id=resource_id,
                value=value,
                request=request,
                dependencies={**dependencies, 'bases': [base.model_dump(mode='json') for base in bases]},
                key_id=identity.key_id if identity else None,
                application_id=identity.application_id if identity else None,
            ),
        )
        return self.store.info(row)

    async def check(self, session: AsyncSession, prepared: PreparedKnowledge, owner: Principal) -> None:
        row = await self.store.get(session, prepared.intent_id)
        if row.status != 'ready' or self.store.state(row).deadline_seconds <= 0:
            raise AppError('preparation_expired', '知识准备已失效', 409)
        await self.store.revalidate(session, owner, prepared.bases, hits=prepared.hits, lock=True)

    async def bind(self, session: AsyncSession, prepared: PreparedKnowledge, owner: Principal, run_id: UUID) -> None:
        for base in prepared.bases:
            sources = tuple(
                source.source
                for source in base.sources
                if any(hit.document_id == source.document_id for hit in prepared.hits)
            )
            await self.knowledge.files.bind_sources(
                session, owner, sources, resource_type='knowledge_run', resource_id=run_id
            )

    async def finalize(self, intent_id: UUID, token: UUID) -> None:
        async with self.database.sessions.begin() as session:
            await lock_billing_admission(session)
            row = await self.store.get(session, intent_id)
            state = self.store.state(row)
            if row.status != 'ready':
                return
            try:
                async with session.begin_nested():
                    if state.key_id is None:
                        await self.lock_main_request(session, state)
                    await self.authorize(session, state, lock=True)
                    if state.key_id is not None:
                        await self.lock_main_request(session, state)
                    bases = self.store.bases(state)
                    main_binding = ModelRequest.model_validate(state.dependencies['main_request']).binding
                    bindings = await self.models.resolve_many(
                        session, sorted({main_binding.model_id, *(base.binding.model_id for base in bases)})
                    )
                    if bindings[main_binding.model_id] != main_binding or any(
                        binding_fingerprint(bindings[base.binding.model_id]) != base.spec.fingerprint for base in bases
                    ):
                        raise AppError('knowledge_preparation_stale', '模型依赖已变化', 409)
                    prepared = PreparedKnowledge(
                        intent_id=state.id, bases=bases, hits=[Hit.model_validate(hit) for hit in row.hits]
                    )
                    run = await self.finalizers[state.resource_type](session, state, prepared)
                    await self.store.admitted(session, state.id, run.id, token)
            except AppError as error:
                row = await self.store.get(session, intent_id, lock=True)
                if row.tick_token == token:
                    await self.store.fail(session, row, 'failed', error.code, error.message)
                    if state.resource_type in self.terminal_callbacks:
                        await self.terminal_callbacks[state.resource_type](session, state)

    async def lock_main_request(self, session: AsyncSession, state: RetrievalState) -> None:
        if state.resource_type == 'work_turn':
            request_id = uuid5(state.resource_id, str(state.request['body']['client_request_id']))
            await self.runs.lock_request(session, state.owner_id, request_id)
        elif state.resource_type == 'chat':
            request_id = UUID(state.request['client_request_id'])
        else:
            request_id = UUID(state.request['runtime_request_id'])
        await self.runs.lock_request(session, state.actor_id, request_id)

    async def query(
        self, session: AsyncSession, principal: Principal, base_id: UUID, body: QueryRequest
    ) -> PreparationInfo:
        await lock_billing_admission(session)
        value = fingerprint(['knowledge_query', str(base_id), body.model_dump(mode='json')])
        await self.runs.lock_request(session, principal.id, body.client_request_id)
        await self.knowledge.authorize_base(session, principal, base_id)
        row = await self.store.existing(session, principal.id, body.client_request_id, value)
        if row is not None:
            return await self.info(session, principal, row.id)
        bases = await self.store.snapshot(session, principal, [base_id])
        for base in bases:
            binding = await self.models.resolve(session, base.binding.model_id)
            if binding_fingerprint(binding) != base.spec.fingerprint:
                raise AppError('knowledge_reindex_required', 'Embedding绑定已变化，请显式重建索引', 409)
        row = await self.store.create(
            session,
            body=RetrievalAdmission(
                namespace_id=principal.id,
                request_id=body.client_request_id,
                actor_id=principal.id,
                owner_id=principal.id,
                resource_type='knowledge_query',
                resource_id=base_id,
                value=value,
                request=body.model_dump(mode='json'),
                dependencies={'bases': [base.model_dump(mode='json') for base in bases]},
            ),
        )
        return self.store.info(row)

    async def authorize(self, session: AsyncSession, state: RetrievalState, *, lock: bool = False) -> Principal:
        principal = await self.knowledge.identity.principal_for_user(session, state.owner_id)
        if state.key_id is not None:
            assert state.application_id is not None
            identity = DeveloperIdentity(state.application_id, state.key_id, state.owner_id)
            required = [base.binding.model_id for base in self.store.bases(state)]
            if 'main_request' in state.dependencies:
                required.append(ModelRequest.model_validate(state.dependencies['main_request']).binding.model_id)
            await self.keys.authorize_identity(session, identity, required, lock=lock)
        if state.resource_type in {'agent_preview', 'agent_invocation'}:
            application_id = state.dependencies.get('config', {}).get('application_id')
            if application_id:
                if state.key_id:
                    await self.applications.require_active_owned(
                        session, state.owner_id, UUID(application_id), lock=lock
                    )
                else:
                    await self.applications.require_owned(session, state.owner_id, UUID(application_id), lock=lock)
        return principal

    async def live_bindings(self, session: AsyncSession, state: RetrievalState) -> None:
        bases = self.store.bases(state)
        main = (
            ModelRequest.model_validate(state.dependencies['main_request']).binding
            if 'main_request' in state.dependencies
            else None
        )
        model_ids = {base.binding.model_id for base in bases}
        if main:
            model_ids.add(main.model_id)
        bindings = await self.models.resolve_many(session, sorted(model_ids))
        if main and bindings[main.model_id] != main:
            raise AppError('knowledge_preparation_stale', '主模型依赖已变化', 409)
        for base in bases:
            if binding_fingerprint(bindings[base.binding.model_id]) != base.spec.fingerprint:
                raise AppError('knowledge_reindex_required', 'Embedding绑定已变化，请显式重建', 409)

    async def check_dependencies(self, session: AsyncSession, state: RetrievalState) -> None:
        if state.resource_type in self.dependency_guards:
            await self.dependency_guards[state.resource_type](session, state)

    async def tick(self, intent_id: UUID) -> None:
        async with self.database.sessions.begin() as session:
            claimed = await self.store.claim(session, intent_id)
        if claimed is None:
            await self.cancel_children_if_terminal(intent_id)
            return
        state, token = claimed
        execution = asyncio.create_task(self._stage(state, token))
        deadline = asyncio.create_task(self._deadline(state, token))
        control = asyncio.create_task(self._observe(state.id, token))
        try:
            done, _ = await asyncio.wait({execution, deadline, control}, return_when=asyncio.FIRST_COMPLETED)
            if control in done and execution not in done:
                await control
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                await self.cancel_children_if_terminal(intent_id)
                return
            if deadline in done:
                await deadline
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                await self.cancel_children_if_terminal(intent_id)
                return
            await execution
        except asyncio.CancelledError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            async with self.database.sessions.begin() as session:
                row = await self.store.get(session, intent_id, lock=True)
                if row.tick_token == token:
                    await self.store.fail(session, row, 'interrupted', 'worker_stopped', '知识准备执行进程停止')
            await self.cancel_children_if_terminal(intent_id)
            raise
        except Exception as error:
            code, message = (
                (error.code, error.message)
                if isinstance(error, AppError)
                else (
                    ('knowledge_timeout', '知识检索超过10秒')
                    if isinstance(error, TimeoutError)
                    else ('knowledge_unavailable', '知识检索未完成')
                )
            )
            async with self.database.sessions.begin() as session:
                row = await self.store.get(session, intent_id, lock=True)
                if row.tick_token == token:
                    await self.store.fail(session, row, 'failed', code, message)
            await self.cancel_children_if_terminal(intent_id)
        finally:
            deadline.cancel()
            control.cancel()
            await asyncio.gather(deadline, control, return_exceptions=True)

    async def _observe(self, intent_id: UUID, token: UUID) -> None:
        while True:
            await asyncio.sleep(0.2)
            async with self.database.sessions.begin() as session:
                state = await self.store.control(session, intent_id, token)
            if state != 'active':
                return

    async def _deadline(self, state: RetrievalState, token: UUID) -> None:
        await asyncio.sleep(max(0.001, state.deadline_seconds))
        async with self.database.sessions.begin() as session:
            row = await self.store.get(session, state.id, lock=True)
            if row.tick_token == token:
                await self.store.fail(session, row, 'failed', 'knowledge_timeout', '知识检索超过10秒')

    async def _stage(self, state: RetrievalState, token: UUID) -> None:
        if state.status == 'ready':
            await self.finalize(state.id, token)
            return
        bases = self.store.bases(state)
        async with self.database.sessions.begin() as session:
            principal = await self.authorize(session, state)
            await self.live_bindings(session, state)
            await self.check_dependencies(session, state)
            await self.store.revalidate(session, principal, bases)
            await self.store.owned(session, state.id, token)
        vectors = await self._embedding_vectors(state, token, bases)
        if vectors is None:
            return
        hits = []
        for base in bases:
            if not base.sources:
                continue
            rows = await self.vectors.search(
                base.spec,
                vectors[base.spec.fingerprint],
                int(state.request.get('top_k', 8)),
                [source.document_id for source in base.sources],
                timeout_seconds=max(0.01, (state.deadline - utcnow()).total_seconds()),
            )
            async with self.database.sessions() as session:
                hits.extend(await self.store.provenance(session, base, rows))
        hits = sorted(hits, key=lambda hit: (-hit.score, str(hit.chunk_id)))
        bounded: list[Hit] = []
        size = 0
        for hit in hits:
            if hit.score < float(state.request.get('similarity_threshold', 0)):
                continue
            cost = len(hit.model_dump_json().encode())
            if size + cost <= MAX_HITS_BYTES:
                bounded.append(hit)
                size += cost
            if len(bounded) >= int(state.request.get('top_k', 8)):
                break
        async with self.database.sessions.begin() as session:
            principal = await self.authorize(session, state, lock=True)
            await self.live_bindings(session, state)
            await self.check_dependencies(session, state)
            await self.store.revalidate(session, principal, bases, hits=bounded, lock=True)
            await self.store.completed(session, state.id, token, bounded)
        if state.resource_type != 'knowledge_query':
            await self.finalize(state.id, token)

    async def _embedding_vectors(
        self, state: RetrievalState, token: UUID, bases: list[RetrievalBase]
    ) -> dict[str, list[float]] | None:
        grouped = {base.spec.fingerprint: base for base in bases if base.sources}
        vectors = {}
        for binding_key, base in grouped.items():
            step_key = 'query_embedding:' + binding_key
            async with self.database.sessions.begin() as session:
                child_id = await self.steps.step(session, state.id, step_key)
            if child_id is None:
                async with self.database.sessions.begin() as session:
                    await lock_billing_admission(session)
                    await self.authorize(session, state, lock=True)
                    await self.models.resolve(session, base.binding.model_id)
                    await self.store.owned(session, state.id, token)
                    request = ModelRequest(
                        binding=base.binding,
                        messages=(ModelMessage('user', str(state.request['query'])),),
                        execution_deadline=state.deadline,
                        execution_timeout_seconds=max(0.1, min(10, state.deadline_seconds)),
                    )
                    value = fingerprint(request.model_dump(mode='json'))
                    request_id = uuid5(state.id, step_key)
                    await self.runs.lock_request(session, state.owner_id, request_id)
                    run = await self.runs.existing_request(session, state.owner_id, request_id, value)
                    if run is None:
                        run = await self.runs.create(
                            session,
                            owner_id=state.owner_id,
                            billing_owner_id=state.owner_id,
                            resource_type='knowledge_embedding',
                            resource_id=state.id,
                            request_id=request_id,
                            fingerprint=value,
                            payload=request.model_dump(mode='json'),
                        )
                        rate = await self.usage.money_rate(
                            session,
                            self.models,
                            base.binding.model_id,
                            required=state.key_id is not None,
                        )
                        await self.usage.reserve(
                            session,
                            user_id=state.owner_id,
                            run_id=run.id,
                            model_id=base.binding.model_id,
                            source='knowledge_retrieval',
                            tokens=len(str(state.request['query']).encode()) + 16,
                            estimated_input_tokens=len(str(state.request['query']).encode()) + 16,
                            max_output_tokens=0,
                            token_limit=self.token_limit,
                            key_id=state.key_id,
                            rate=rate,
                            operation='embedding',
                        )
                    await self.steps.add_step(session, state.id, step_key, run.id, value)
                    await self.store.release(session, state.id, token, 'waiting_models')
                return None
            async with self.database.sessions() as session:
                run = await self.runs.resource_snapshot(session, child_id, 'knowledge_embedding', state.id)
            if run.status not in TERMINAL_STATUSES:
                async with self.database.sessions.begin() as session:
                    await self.store.release(session, state.id, token, 'waiting_models')
                return None
            if run.status != 'completed':
                raise AppError('knowledge_embedding_failed', '检索Embedding执行失败或结果不确定', 502)
            vectors[binding_key] = validate_vectors(run.result, 1, base.spec.dimension)[0]
        return vectors

    async def info(self, session: AsyncSession, principal: Principal, intent_id: UUID) -> PreparationInfo:
        row = await self.store.get(session, intent_id)
        if row.key_id is not None or not self.can_observe(self.store.state(row), principal):
            raise AppError('preparation_not_found', '知识准备不存在', 404)
        state = self.store.state(row)
        if row.resource_type == 'knowledge_query':
            await self.knowledge.authorize_base(session, principal, row.resource_id)
        if row.hits and row.main_run_id is None:
            owner = await self.authorize(session, state)
            # Reading a completed result must still enforce current source ACL.
            for hit in row.hits:
                await self.knowledge.authorize_base(session, owner, UUID(str(hit['knowledge_base_id'])))
                await self.knowledge.document_info(
                    session, UUID(str(hit['knowledge_base_id'])), UUID(str(hit['document_id']))
                )
                await self.knowledge.files.require(session, owner, UUID(str(hit['file_id'])), active=True)
        run = await self.runs.projection(session, row.main_run_id) if row.main_run_id else None
        result = self.store.info(row, run)
        if row.main_run_id:
            result.hits = await self.owner_visible_hits(session, row.owner_id, result.hits)
        return result

    @staticmethod
    def can_observe(state: RetrievalState, principal: Principal) -> bool:
        if state.resource_type == 'chat':
            return state.owner_id == principal.id
        if state.resource_type == 'work_turn':
            return state.owner_id == principal.id or principal.is_admin
        return state.actor_id == principal.id or principal.is_admin

    async def owner_visible_hits(self, session: AsyncSession, owner_id: UUID, hits: list[Hit]) -> list[Hit]:
        if not await self.knowledge.identity.is_active(session, owner_id):
            return []
        owner = await self.knowledge.identity.principal_for_user(session, owner_id)
        return await self.visible_hits(session, owner, hits)

    async def visible_hits(self, session: AsyncSession, principal: Principal, hits: list[Hit]) -> list[Hit]:
        visible = []
        for hit in hits:
            try:
                await self.knowledge.authorize_base(session, principal, hit.knowledge_base_id)
                await self.knowledge.document_info(session, hit.knowledge_base_id, hit.document_id)
                await self.knowledge.files.require(session, principal, hit.file_id, active=True)
            except AppError:
                continue
            visible.append(hit)
        return visible

    async def public_info(
        self, session: AsyncSession, identity: DeveloperIdentity, agent_id: UUID, intent_id: UUID
    ) -> PreparationInfo:
        row = await self.store.get(session, intent_id)
        if (
            row.key_id != identity.key_id
            or row.application_id != identity.application_id
            or row.resource_id != agent_id
        ):
            raise AppError('preparation_not_found', 'Preparation is unavailable', 404)
        if row.main_run_id is None:
            await self.authorize(session, self.store.state(row))
        else:
            await self.authorize_public_run(session, identity, agent_id, row.main_run_id)
        run = await self.runs.projection(session, row.main_run_id) if row.main_run_id else None
        result = self.store.info(row, run)
        if row.hits:
            result.hits = await self.owner_visible_hits(session, row.owner_id, result.hits)
        return result

    async def authorize_public_run(
        self, session: AsyncSession, identity: DeveloperIdentity, agent_id: UUID, run_id: UUID
    ) -> UUID:
        payload = await self.runs.resource_payload(session, run_id, 'agent_invocation', agent_id)
        model_id = ModelRequest.model_validate(payload).binding.model_id
        await self.keys.authorize_identity(session, identity, [model_id])
        return model_id

    async def cancel(self, session: AsyncSession, principal: Principal, intent_id: UUID) -> PreparationInfo:
        row = await self.store.get(session, intent_id, lock=True)
        if row.key_id is not None or not self.can_observe(self.store.state(row), principal):
            raise AppError('preparation_not_found', '知识准备不存在', 404)
        if row.main_run_id:
            return await self.cancel_main(session, intent_id)
        await self.store.fail(session, row, 'cancelled', 'knowledge_cancelled', '知识准备已取消')
        row = await self.store.get(session, intent_id)
        result = self.store.info(row)
        result.hits = await self.owner_visible_hits(session, row.owner_id, result.hits)
        return result

    async def cancel_main(self, session: AsyncSession, intent_id: UUID) -> PreparationInfo:
        row = await self.store.get(session, intent_id)
        assert row.main_run_id is not None
        snapshot, release = await self.runs.resource_cancel(
            session, row.main_run_id, row.resource_type, row.resource_id
        )
        if release:
            await self.usage.settle(session, row.main_run_id, rejected_before_execution=True)
        await RunLifecycle(ChatService()).project(session, snapshot)
        result = self.store.info(row, snapshot)
        result.hits = await self.owner_visible_hits(session, row.owner_id, result.hits)
        return result

    async def cancel_children_if_terminal(self, intent_id: UUID) -> None:
        async with self.database.sessions() as session:
            row = await self.store.get(session, intent_id)
            if row.status not in {'failed', 'cancelled', 'interrupted'}:
                return
            steps = await self.steps.children(session, intent_id)
            owner_id = row.owner_id
        for child_id in steps:
            async with self.database.sessions.begin() as session:
                _, release = await self.runs.cancel(session, child_id, owner_id)
                if release:
                    await self.usage.settle(session, child_id, rejected_before_execution=True)
        if row.resource_type in self.terminal_callbacks:
            async with self.database.sessions.begin() as session:
                await self.terminal_callbacks[row.resource_type](session, self.store.state(row))
