"""Durable, paid-call-idempotent retrieval before final model admission."""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.execution.lifecycle import RunLifecycle
from zhiheng.infrastructure.database import Database
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.developer.public import ApplicationService, DeveloperIdentity, KeyService, Scope
from zhiheng.modules.knowledge.public import (
    FINAL_HITS,
    PER_BASE_CANDIDATES,
    BuildStore,
    Hit,
    KnowledgeService,
    PreparationInfo,
    PreparedKnowledge,
    QueryRequest,
    RetrievalAdmission,
    RetrievalBase,
    RetrievalState,
    RetrievalStore,
    VectorPort,
    balance_candidates,
    binding_fingerprint,
    fingerprint,
    rank_candidates,
    validate_vectors,
)
from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelRequest, ModelService
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability, require_execution_capabilities
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
        vectors: VectorPort,
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
        self.resource_guard: Callable[[AsyncSession, RetrievalState], Awaitable[None]] | None = None

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
        if identity is None:
            require_execution_capabilities(
                principal, body.resource_type, knowledge=True, skills=bool(body.request.get('skill_slugs'))
            )
        if identity:
            if body.owner_id != identity.owner_id or principal.id != identity.owner_id:
                raise AppError('invalid_api_origin', 'Key preparation must use its current owner', 403)
            await self.keys.authorize_identity(
                session, identity, [], scope=self.identity_scope(body.resource_type), lock=True
            )
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
        require_capability(owner, 'knowledge', 'access')
        bases = await self.store.snapshot(session, owner, base_ids)
        bindings = await self.models.resolve_many(session, sorted({base.binding.model_id for base in bases}))
        for base in bases:
            current = bindings[base.binding.model_id]
            if binding_fingerprint(current) != base.spec.fingerprint:
                raise AppError('knowledge_reindex_required', 'Embedding绑定已变化，请显式重建索引', 409)
        rerank = await self.knowledge.retrieval_rerank_binding(session, base_ids)
        if identity:
            await self.keys.authorize_identity(
                session,
                identity,
                [base.binding.model_id for base in bases] + [rerank.model_id],
                scope=self.identity_scope(resource_type),
                lock=True,
            )
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
                dependencies={
                    **dependencies,
                    'bases': [base.model_dump(mode='json') for base in bases],
                    'rerank_binding': rerank.model_dump(mode='json'),
                },
                key_id=identity.key_id if identity else None,
                application_id=identity.application_id if identity else None,
            ),
        )
        return self.store.info(row)

    async def require_run_bases(self, session: AsyncSession, run_id: UUID, resource_id: UUID) -> None:
        """A queued main generation still requires current library access after retrieval admission."""
        state = await self.store.state_for_run(session, run_id, resource_id)
        if state is None:
            return
        owner = await self.knowledge.identity.principal_for_user(session, state.owner_id)
        require_capability(owner, 'knowledge', 'access')
        for base in self.store.bases(state):
            await self.knowledge.authorize_base(session, owner, base.base_id, lock=True)

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
                    await self.live_bindings(session, state)
                    if state.key_id is not None:
                        await self.lock_main_request(session, state)
                    bases = self.store.bases(state)
                    if not bases and row.hits:
                        raise AppError('knowledge_preparation_stale', '检索资料依赖已变化，请重新发起检索', 409)
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
        self,
        session: AsyncSession,
        principal: Principal,
        base_id: UUID,
        body: QueryRequest,
        *,
        identity: DeveloperIdentity | None = None,
        namespace_id: UUID | None = None,
    ) -> PreparationInfo:
        await lock_billing_admission(session)
        value = fingerprint(['knowledge_query', str(base_id), body.model_dump(mode='json')])
        namespace = namespace_id or principal.id
        await self.runs.lock_request(session, namespace, body.client_request_id)
        await self.knowledge.authorize_base(session, principal, base_id)
        row = await self.store.existing(session, namespace, body.client_request_id, value)
        if row is not None:
            return self.store.info(row) if identity else await self.info(session, principal, row.id)
        return await self.start(
            session,
            principal=principal,
            identity=identity,
            body=PreparationStart(
                owner_id=principal.id,
                resource_type='knowledge_query',
                resource_id=base_id,
                request_id=body.client_request_id,
                value=value,
                request=body.model_dump(mode='json'),
                base_ids=[base_id],
                dependencies={},
                namespace_id=namespace,
            ),
        )

    async def admission_rate(
        self, session: AsyncSession, model_id: UUID, identity: DeveloperIdentity, tokens: int = 1
    ) -> None:
        await self.usage.preflight(
            session,
            self.models,
            user_id=identity.owner_id,
            key_id=identity.key_id,
            model_id=model_id,
            input_tokens=tokens,
            token_limit=self.token_limit,
        )

    @staticmethod
    def identity_scope(resource_type: str) -> Scope:
        if resource_type == 'agent_invocation':
            return 'agents:invoke'
        if resource_type == 'knowledge_query':
            return 'knowledge:query'
        raise AppError('invalid_api_origin', 'Unsupported Key preparation origin', 403)

    async def authorize(self, session: AsyncSession, state: RetrievalState, *, lock: bool = False) -> Principal:
        principal = await self.knowledge.identity.principal_for_user(session, state.owner_id)
        if state.key_id is None:
            require_execution_capabilities(
                principal, state.resource_type, knowledge=True, skills=bool(state.request.get('skill_slugs'))
            )
        if state.key_id is not None:
            assert state.application_id is not None
            identity = DeveloperIdentity(state.application_id, state.key_id, state.owner_id)
            required = [base.binding.model_id for base in self.store.bases(state)]
            rerank = self.rerank_binding(state)
            if rerank is not None:
                required.append(rerank.model_id)
            if 'main_request' in state.dependencies:
                required.append(ModelRequest.model_validate(state.dependencies['main_request']).binding.model_id)
            await self.keys.authorize_identity(
                session, identity, required, scope=self.identity_scope(state.resource_type), lock=lock
            )
        if self.resource_guard is not None:
            await self.resource_guard(session, state)
        if state.key_id is not None and state.resource_type == 'knowledge_query':
            principal = principal.personal()
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
        rerank = self.rerank_binding(state)
        if rerank is None and bases:
            raise AppError('knowledge_rerank_required', '检索缺少重排模型，请配置后重新发起检索', 409)
        if rerank is not None:
            model_ids.add(rerank.model_id)
        if main:
            model_ids.add(main.model_id)
        bindings = await self.models.resolve_many(session, sorted(model_ids))
        if rerank is not None and bindings[rerank.model_id] != rerank:
            raise AppError('knowledge_preparation_stale', '重排模型依赖已变化', 409)
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
        frozen = state.dependencies.get('retrieval_candidates')
        if frozen is None:
            vectors = await self._embedding_vectors(state, token, bases)
            if vectors is None:
                return
            hits = await self._recall(state, bases, vectors)
            diagnostics: dict[str, object] = {
                'candidate_count': len(hits),
                'rerank_batch_budget': max(1, min(48000, self.token_limit // 2)),
                'retrieval_modes': {str(base.base_id): 'hybrid' if base.spec.hybrid else 'vector' for base in bases},
                'rerank_status': 'no_candidates' if not hits else 'pending',
            }
            async with self.database.sessions.begin() as session:
                principal = await self.authorize(session, state, lock=True)
                await self.live_bindings(session, state)
                await self.check_dependencies(session, state)
                await self.store.revalidate(session, principal, bases, hits=hits, lock=True)
                await self.store.freeze_candidates(session, state.id, token, hits, diagnostics)
            state.dependencies.update(
                retrieval_candidates=[hit.model_dump(mode='json') for hit in hits], retrieval_diagnostics=diagnostics
            )
        else:
            hits = [Hit.model_validate(hit) for hit in frozen]
        ranked = await self._rerank(state, token, bases, hits)
        if ranked is None:
            return
        bounded: list[Hit] = []
        size = 0
        for hit in ranked:
            cost = len(hit.model_dump_json().encode())
            if size + cost <= MAX_HITS_BYTES:
                bounded.append(hit)
                size += cost
            if len(bounded) >= min(FINAL_HITS, int(state.request.get('top_k', FINAL_HITS))):
                break
        async with self.database.sessions.begin() as session:
            principal = await self.authorize(session, state, lock=True)
            await self.live_bindings(session, state)
            await self.check_dependencies(session, state)
            await self.store.revalidate(session, principal, bases, hits=bounded, lock=True)
            await self.store.completed(session, state.id, token, bounded)
        if state.resource_type != 'knowledge_query':
            await self.finalize(state.id, token)

    async def _recall(
        self, state: RetrievalState, bases: list[RetrievalBase], vectors: dict[str, list[float]]
    ) -> list[Hit]:
        groups = []
        for base in bases:
            if not base.sources:
                continue
            arguments = (base.spec, vectors[base.spec.fingerprint])
            documents = [source.document_id for source in base.sources]
            timeout = max(0.01, (state.deadline - utcnow()).total_seconds())
            if base.spec.hybrid:
                rows = await self.vectors.hybrid_search(
                    *arguments,
                    str(state.request['query']),
                    PER_BASE_CANDIDATES,
                    documents,
                    timeout_seconds=timeout,
                )
            else:
                rows = await self.vectors.search(
                    *arguments,
                    PER_BASE_CANDIDATES,
                    documents,
                    timeout_seconds=timeout,
                )
            async with self.database.sessions() as session:
                candidates = await self.store.provenance(session, base, rows)
            # COSINE thresholds apply only to the legacy dense channel.
            if base.spec.retrieval_version == 1:
                candidates = [
                    hit for hit in candidates if hit.score >= float(state.request.get('similarity_threshold', 0))
                ]
            groups.append(candidates[:PER_BASE_CANDIDATES])
        return balance_candidates(groups)

    @staticmethod
    def rerank_binding(state: RetrievalState) -> ModelBinding | None:
        value = state.dependencies.get('rerank_binding')
        return ModelBinding.model_validate(value) if value is not None else None

    async def _rerank(
        self, state: RetrievalState, token: UUID, bases: list[RetrievalBase], candidates: list[Hit]
    ) -> list[Hit] | None:
        if not candidates:
            return []
        diagnostics = dict(state.dependencies.get('retrieval_diagnostics', {}))
        batches = self.rerank_batches(state, candidates)
        ranked: list[Hit] = []
        for index, batch in enumerate(batches):
            key = 'query_rerank' if index == 0 else f'query_rerank:{index}'
            result = await self._rerank_batch(state, token, bases, batch, key)
            if result is None:
                return None
            ranked.extend(result)
        async with self.database.sessions.begin() as session:
            ids = [
                await self.steps.step(session, state.id, 'query_rerank' if index == 0 else f'query_rerank:{index}')
                for index in range(len(batches))
            ]
            await self.store.diagnostics(
                session,
                state.id,
                token,
                {
                    **diagnostics,
                    'rerank_status': 'completed',
                    'rerank_batch_count': len(batches),
                    'rerank_run_ids': [str(value) for value in ids],
                    'rerank_run_id': str(ids[0]),
                },
            )
        order = {hit.chunk_id: index for index, hit in enumerate(candidates)}
        ranked.sort(key=lambda hit: (-(hit.rerank_score or 0), order[hit.chunk_id]))
        return ranked[: min(FINAL_HITS, int(state.request.get('top_k', FINAL_HITS)))]

    @staticmethod
    def rerank_batches(state: RetrievalState, candidates: list[Hit]) -> list[list[Hit]]:
        # UTF-8 bytes are a conservative estimate. Bound each paid step rather than
        # dropping sources or reserving the entire multilingual corpus at once.
        query_size = len(str(state.request['query']).encode()) + 16
        budget = int(
            dict(state.dependencies.get('retrieval_diagnostics', {})).get(
                'rerank_batch_budget', sum(len(hit.content.encode()) + 16 for hit in candidates) + query_size
            )
        )
        batches: list[list[Hit]] = [[]]
        size = query_size
        for hit in candidates:
            cost = len(hit.content.encode()) + 16
            if batches[-1] and size + cost > budget:
                batches.append([])
                size = query_size
            batches[-1].append(hit)
            size += cost
        return batches

    async def rerank_candidates_for_run(self, session: AsyncSession, state: RetrievalState, run_id: UUID) -> list[Hit]:
        candidates = [Hit.model_validate(hit) for hit in state.dependencies.get('retrieval_candidates', [])]
        for index, batch in enumerate(self.rerank_batches(state, candidates)):
            key = 'query_rerank' if index == 0 else f'query_rerank:{index}'
            if await self.steps.step(session, state.id, key) == run_id:
                return batch
        raise AppError('knowledge_preparation_stale', '重排运行不属于冻结批次', 409)

    async def _rerank_batch(
        self,
        state: RetrievalState,
        token: UUID,
        bases: list[RetrievalBase],
        candidates: list[Hit],
        step_key: str = 'query_rerank',
    ) -> list[Hit] | None:
        binding = self.rerank_binding(state)
        if binding is None:
            raise AppError('knowledge_rerank_required', '检索缺少重排模型，请重新发起检索', 409)
        if not candidates:
            return []
        async with self.database.sessions.begin() as session:
            child_id = await self.steps.step(session, state.id, step_key)
        if child_id is None:
            async with self.database.sessions.begin() as session:
                await lock_billing_admission(session)
                principal = await self.authorize(session, state, lock=True)
                await self.live_bindings(session, state)
                await self.check_dependencies(session, state)
                await self.store.revalidate(session, principal, bases, hits=candidates, lock=True)
                await self.store.owned(session, state.id, token)
                request = ModelRequest(
                    binding=binding.model_copy(
                        update={
                            'options': binding.options.model_copy(
                                update={
                                    'top_n': min(FINAL_HITS, int(state.request.get('top_k', FINAL_HITS))),
                                    'return_documents': False,
                                }
                            )
                        }
                    ),
                    messages=(
                        ModelMessage('user', str(state.request['query'])),
                        *(ModelMessage('user', hit.content) for hit in candidates),
                    ),
                    execution_deadline=state.deadline,
                    execution_timeout_seconds=max(0.1, min(5, (state.deadline - utcnow()).total_seconds() - 0.3)),
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
                        resource_type='knowledge_rerank',
                        resource_id=state.id,
                        request_id=request_id,
                        fingerprint=value,
                        payload=request.model_dump(mode='json'),
                    )
                    rate = await self.usage.money_rate(
                        session, self.models, binding.model_id, required=state.key_id is not None
                    )
                    input_tokens = sum(len(message.content.encode()) + 16 for message in request.messages)
                    await self.usage.reserve(
                        session,
                        user_id=state.owner_id,
                        run_id=run.id,
                        model_id=binding.model_id,
                        source='knowledge_retrieval',
                        tokens=input_tokens,
                        estimated_input_tokens=input_tokens,
                        max_output_tokens=0,
                        token_limit=self.token_limit,
                        key_id=state.key_id,
                        rate=rate,
                        operation='rerank',
                        estimated_search_units=len(candidates),
                    )
                await self.steps.add_step(session, state.id, step_key, run.id, value)
                await self.store.diagnostics(
                    session,
                    state.id,
                    token,
                    {**dict(state.dependencies.get('retrieval_diagnostics', {})), 'rerank_status': 'pending'},
                )
                await self.store.release(session, state.id, token, 'waiting_models')
            return None
        async with self.database.sessions() as session:
            run = await self.runs.resource_snapshot(session, child_id, 'knowledge_rerank', state.id)
        if run.status not in TERMINAL_STATUSES:
            async with self.database.sessions.begin() as session:
                await self.store.diagnostics(
                    session,
                    state.id,
                    token,
                    {**dict(state.dependencies.get('retrieval_diagnostics', {})), 'rerank_status': 'pending'},
                )
                await self.store.release(session, state.id, token, 'waiting_models')
            return None
        diagnostics = dict(state.dependencies.get('retrieval_diagnostics', {}))
        diagnostics.update(
            rerank_run_id=str(run.id),
            rerank_milliseconds=max(0, int((utcnow() - run.created_at).total_seconds() * 1000)),
        )
        if run.status == 'completed':
            ranked = rank_candidates(candidates, run.result)
            diagnostics['rerank_status'] = 'completed'
        else:
            ranked = []
            diagnostics.update(rerank_status='failed', rerank_reason=run.error.code if run.error else run.status)
        # Revalidate access before publishing either a result or failure diagnostics.
        async with self.database.sessions.begin() as session:
            principal = await self.authorize(session, state, lock=True)
            await self.live_bindings(session, state)
            await self.check_dependencies(session, state)
            await self.store.revalidate(session, principal, bases, hits=candidates, lock=True)
            await self.store.diagnostics(session, state.id, token, diagnostics)
        if run.status != 'completed':
            raise AppError('knowledge_rerank_failed', '重排执行失败或结果不确定，本次未返回检索结果', 502)
        return ranked

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
                    await self.live_bindings(session, state)
                    await self.check_dependencies(session, state)
                    owner = await self.knowledge.identity.principal_for_user(session, state.owner_id)
                    await self.store.revalidate(session, owner, bases)
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
                raise AppError(
                    'knowledge_embedding_failed',
                    '知识库向量模型调用失败，本次检索已停止。请稍后重试；若持续失败，请检查 Embedding 模型连接。',
                    502,
                )
            vectors[binding_key] = validate_vectors(run.result, 1, base.spec.dimension)[0]
        return vectors

    async def info(self, session: AsyncSession, principal: Principal, intent_id: UUID) -> PreparationInfo:
        row = await self.store.get(session, intent_id)
        if row.key_id is not None or not self.can_observe(self.store.state(row), principal):
            raise AppError('preparation_not_found', '知识准备不存在', 404)
        state = self.store.state(row)
        require_capability(
            principal,
            {'chat': 'chat', 'agent_chat': 'agents', 'agent_preview': 'agents', 'work_turn': 'work'}.get(
                state.resource_type, 'knowledge'
            ),
        )
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
                await self.knowledge.require_document_source(
                    session, owner, UUID(str(hit['knowledge_base_id'])), UUID(str(hit['document_id']))
                )
        run = await self.runs.projection(session, row.main_run_id) if row.main_run_id else None
        result = self.store.info(row, run)
        for base in self.store.bases(state):
            current = await self.knowledge.require(session, principal, base.base_id)
            if not self.knowledge.manages(principal, current):
                result.diagnostics = {}
                break
        if row.main_run_id:
            result.hits = await self.owner_visible_hits(session, row.owner_id, result.hits)
        return result

    @staticmethod
    def can_observe(state: RetrievalState, principal: Principal) -> bool:
        if state.resource_type == 'chat':
            return state.owner_id == principal.id
        if state.resource_type == 'knowledge_query':
            # Access to a base does not transfer another actor's query or its frozen hits.
            # Apply the same ownership boundary to polling and cancellation, including admins.
            return state.actor_id == principal.id
        if state.resource_type == 'work_turn':
            return state.owner_id == principal.id
        return state.owner_id == state.actor_id == principal.id

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
                await self.knowledge.require_document_source(session, principal, hit.knowledge_base_id, hit.document_id)
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
        result.diagnostics = {}
        if row.hits:
            result.hits = await self.owner_visible_hits(session, row.owner_id, result.hits)
        return result

    async def authorize_public_run(
        self, session: AsyncSession, identity: DeveloperIdentity, agent_id: UUID, run_id: UUID
    ) -> UUID:
        payload = await self.runs.resource_payload(session, run_id, 'agent_invocation', agent_id)
        model_id = ModelRequest.model_validate(payload).binding.model_id
        await self.keys.authorize_identity(session, identity, [model_id], scope='agents:invoke')
        return model_id

    async def cancel(self, session: AsyncSession, principal: Principal, intent_id: UUID) -> PreparationInfo:
        row = await self.store.get(session, intent_id, lock=True)
        if row.key_id is not None or not self.can_observe(self.store.state(row), principal):
            raise AppError('preparation_not_found', '知识准备不存在', 404)
        require_capability(
            principal,
            {'chat': 'chat', 'agent_chat': 'agents', 'agent_preview': 'agents', 'work_turn': 'work'}.get(
                row.resource_type, 'knowledge'
            ),
        )
        result = await self._cancel_authorized(session, intent_id, '知识准备已取消')
        result.hits = await self.owner_visible_hits(session, row.owner_id, result.hits)
        return result

    async def cancel_public(
        self, session: AsyncSession, identity: DeveloperIdentity, agent_id: UUID, intent_id: UUID
    ) -> PreparationInfo:
        row = await self.store.get(session, intent_id, lock=True)
        if (
            row.key_id != identity.key_id
            or row.application_id != identity.application_id
            or row.resource_id != agent_id
        ):
            raise AppError('preparation_not_found', 'Preparation is unavailable', 404)
        await self.public_info(session, identity, agent_id, intent_id)
        return await self._cancel_authorized(session, intent_id, 'Knowledge preparation cancelled')

    async def _cancel_authorized(self, session: AsyncSession, intent_id: UUID, message: str) -> PreparationInfo:
        row = await self.store.get(session, intent_id)
        if row.main_run_id:
            return await self.cancel_main(session, intent_id)
        await self.store.fail(session, row, 'cancelled', 'knowledge_cancelled', message)
        row = await self.store.get(session, intent_id)
        return self.store.info(row)

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
