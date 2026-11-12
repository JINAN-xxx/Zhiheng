"""Revalidate execution authority and immutable model/source snapshots before provider use."""

from zhiheng.application.access import ResourceAccessService
from zhiheng.application.chat.context import ChatContextService
from zhiheng.application.execution.content_access import RunContentAccess
from zhiheng.application.knowledge.graph.runs import GraphRuns
from zhiheng.application.knowledge.jobs import KnowledgeJobs
from zhiheng.application.knowledge.origin import KnowledgeOriginGuard
from zhiheng.application.knowledge.preparation import KnowledgePreparation
from zhiheng.infrastructure.database import Database
from zhiheng.modules.developer.public import KeyService
from zhiheng.modules.files.public import FileService
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.public import PREPARATION_TERMINAL, binding_fingerprint
from zhiheng.modules.models.public import ModelBinding, ModelProvider, ModelRequest, ModelService
from zhiheng.runtime.contracts import ClaimedRun
from zhiheng.shared.errors import AppError


class ModelExecutionAuthorization:
    def __init__(  # noqa: PLR0913 - explicit dependencies for each execution resource policy
        self,
        *,
        database: Database,
        chat_context: ChatContextService,
        keys: KeyService,
        graph_runs: GraphRuns,
        run_content: RunContentAccess,
        knowledge_preparation: KnowledgePreparation,
        knowledge_origin: KnowledgeOriginGuard,
        knowledge_jobs: KnowledgeJobs,
        identity: IdentityService,
        models: ModelService,
        resource_access: ResourceAccessService,
        files: FileService,
    ) -> None:
        self.database, self.chat_context, self.keys = database, chat_context, keys
        self.graph_runs, self.knowledge_preparation = graph_runs, knowledge_preparation
        self.run_content = run_content
        self.knowledge_origin, self.knowledge_jobs = knowledge_origin, knowledge_jobs
        self.identity, self.models, self.resource_access, self.files = identity, models, resource_access, files

    async def prepare_provider(self, binding: ModelBinding) -> ModelProvider:
        async with self.database.sessions() as session:
            return await self.models.prepare_provider(session, binding)

    async def _require_test(self, claim: ClaimedRun) -> None:
        async with self.database.sessions.begin() as session:
            principal = await self.identity.principal_for_user(session, claim.owner_id)
            expected = ModelRequest.model_validate(claim.payload).binding
            if claim.resource_id != expected.model_id:
                raise AppError('model_test_changed', '试用请求与模型不匹配', 409)
            current = await self.models.resolve_for_test(session, principal, expected.model_id)
            if current.connection_id != expected.connection_id or current.model_type != expected.model_type:
                raise AppError('model_test_changed', '模型连接已变化，请重新试用', 409)

    async def _require_knowledge_run(self, claim: ClaimedRun) -> None:
        if claim.resource_type in {'chat', 'agent_preview', 'agent_invocation', 'work_turn'}:
            async with self.database.sessions.begin() as session:
                if claim.resource_type in {'agent_preview', 'agent_invocation', 'work_turn'}:
                    principal = await self.identity.principal_for_user(session, claim.owner_id)
                    await self.run_content.require_personal(session, principal, claim.resource_type, claim.resource_id)
                await self.knowledge_preparation.require_run_bases(session, claim.id, claim.resource_id)

    async def require(self, claim: ClaimedRun) -> None:
        await self._require_knowledge_run(claim)
        guards = {
            'connection_test': self._require_test,
            'chat': self.chat_context.guard,
            'chat_summary': self.chat_context.guard,
        }
        if guard := guards.get(claim.resource_type):
            await guard(claim)
            return
        if claim.resource_type in {'public', 'agent_invocation'}:
            async with self.database.sessions.begin() as session:
                await self.keys.authorize_run(
                    session,
                    claim.id,
                    scope='agents:invoke' if claim.resource_type == 'agent_invocation' else 'models:invoke',
                )
            return
        if claim.resource_type == 'knowledge_graph':
            await self.graph_runs.guard(claim)
            return
        if claim.resource_type not in {'knowledge_embedding', 'knowledge_rerank'}:
            return
        async with self.database.sessions.begin() as session:
            state = await self.knowledge_preparation.store.find_state(session, claim.resource_id)
            if state is not None:
                if (
                    state.status in PREPARATION_TERMINAL
                    or state.status == 'cancel_requested'
                    or state.deadline_seconds <= 0
                ):
                    raise AppError('knowledge_execution_cancelled', '知识模型步骤已失效', 409)
                owner = await self.knowledge_preparation.authorize(session, state, lock=True)
                await self.knowledge_preparation.live_bindings(session, state)
                await self.knowledge_preparation.check_dependencies(session, state)
                bases = self.knowledge_preparation.store.bases(state)
                await self.knowledge_preparation.store.revalidate(session, owner, bases)
                if claim.resource_type == 'knowledge_rerank':
                    expected = self.knowledge_preparation.rerank_binding(state)
                    request = ModelRequest.model_validate(claim.payload)
                    top_n = min(8, int(state.request.get('top_k', 8)))
                    expected = (
                        expected.model_copy(
                            update={
                                'options': expected.options.model_copy(
                                    update={
                                        'top_n': top_n,
                                        'return_documents': False,
                                    }
                                )
                            }
                        )
                        if expected is not None
                        else None
                    )
                    candidates = await self.knowledge_preparation.rerank_candidates_for_run(session, state, claim.id)
                    if (
                        expected is None
                        or request.binding != expected
                        or [message.content for message in request.messages]
                        != [str(state.request['query']), *(hit.content for hit in candidates)]
                    ):
                        raise AppError('knowledge_preparation_stale', '重排步骤与冻结候选不匹配', 409)
            else:
                if claim.resource_type == 'knowledge_rerank':
                    raise AppError('knowledge_execution_cancelled', '知识重排步骤已失效', 409)
                origin = await self.knowledge_origin.job(
                    session, claim.resource_id, model_ids=(ModelRequest.model_validate(claim.payload).binding.model_id,)
                )
                owner_id, expected, file_id, sha256 = await self.knowledge_jobs.store.model_guard(
                    session, claim.resource_id, claim.id
                )
                owner = await self.identity.principal_for_user(session, owner_id)
                binding = await self.models.resolve(session, expected.model_id)
                if origin is not None:
                    await self.knowledge_jobs.store.model_guard(session, claim.resource_id, claim.id, lock=True)
                    await self.knowledge_origin.job(
                        session, claim.resource_id, model_ids=(expected.model_id,), lock=True
                    )
                    owner = self.resource_access.personal(owner)
                source = await self.files.require(session, owner, file_id, active=True)
                if binding_fingerprint(binding) != binding_fingerprint(expected) or source.sha256 != sha256:
                    raise AppError('knowledge_preparation_stale', '知识模型或来源已变化', 409)
