from redis.asyncio import Redis

from zhiheng.application.agent_management import AgentManagementService
from zhiheng.application.agent_runs import AgentRunService
from zhiheng.application.chat_runs import ChatRunService
from zhiheng.application.file_inputs import FileInputService
from zhiheng.application.graph_jobs import GraphJobs
from zhiheng.application.graph_runs import GraphRuns
from zhiheng.application.knowledge_jobs import KnowledgeJobs
from zhiheng.application.knowledge_preparation import KnowledgePreparation
from zhiheng.application.model_management import ModelManagementService
from zhiheng.application.public_runs import PublicRunService
from zhiheng.application.skill_management import SkillManagementService
from zhiheng.application.work_runs import WorkRunService
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.infrastructure.database import Database
from zhiheng.infrastructure.file_storage import LocalFileStorage
from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.developer.public import ApplicationService, KeyService, UsageService
from zhiheng.modules.files.public import FileJobs, FileService
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.public import (
    PREPARATION_TERMINAL,
    GraphSDK,
    GraphService,
    GraphStore,
    KnowledgeService,
    MilvusStore,
    NativeParser,
    binding_fingerprint,
)
from zhiheng.modules.models.public import ModelBinding, ModelProvider, ModelService
from zhiheng.modules.skills.public import SkillService
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.contracts import ClaimedRun
from zhiheng.runtime.outbox import OutboxStore
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = Database(settings.database_url.get_secret_value())
        self.redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
        self.cipher = CredentialCipher(settings.credential_key.get_secret_value())
        self.identity = IdentityService(settings.session_ttl_seconds)
        self.files = FileService(LocalFileStorage(settings.files_root), self.identity)
        self.file_jobs = FileJobs(self.files, self.database.sessions)
        self.file_inputs = FileInputService(self.database, self.files, NativeParser())
        self.models = ModelService(self.cipher, settings.provider_timeout_seconds)
        self.knowledge = KnowledgeService(self.files, self.identity, self.models)
        self.usage = UsageService()
        self.applications = ApplicationService()
        self.keys = KeyService(self.applications, self.models, self.identity)
        self.runs = RunStore()
        self.work = WorkService()
        self.work_runs = WorkRunService(self.work, AgentService(), self.models, self.identity, self.usage, self.runs)
        self.agents = AgentService()
        self.model_management = ModelManagementService(
            self.models, self.runs, self.keys, self.agents, knowledge=self.knowledge
        )
        self.skills = SkillService()
        self.skill_management = SkillManagementService(self.skills, self.agents)
        self.agent_management = AgentManagementService(self.agents, self.applications, self.models)
        self.agent_runs = AgentRunService(
            self.agents, self.agent_management, self.keys, self.models, self.usage, self.runs
        )
        self.chat = ChatService()
        self.admission = ChatRunService(self.chat, self.models, self.usage, self.runs, self.files)
        self.vectors = MilvusStore(
            settings.milvus_uri, settings.milvus_token.get_secret_value(), settings.milvus_database
        )
        self.knowledge_jobs = KnowledgeJobs(
            self.database,
            self.knowledge,
            self.file_inputs,
            self.files,
            self.models,
            self.runs,
            self.usage,
            self.vectors,
            settings.token_limit,
        )
        self.knowledge_preparation = KnowledgePreparation(
            self.database,
            self.knowledge,
            self.models,
            self.runs,
            self.usage,
            self.keys,
            self.applications,
            self.vectors,
            settings.token_limit,
        )
        self.admission.knowledge, self.admission.file_inputs = self.knowledge_preparation, self.file_inputs
        self.knowledge_preparation.finalizers['chat'] = self.admission.finalize_knowledge
        self.agent_runs.knowledge = self.knowledge_preparation
        self.agent_management.knowledge = self.knowledge_preparation
        self.knowledge_preparation.finalizers['agent_preview'] = self.agent_runs.finalize_knowledge
        self.knowledge_preparation.finalizers['agent_invocation'] = self.agent_runs.finalize_knowledge
        self.work_runs.knowledge = self.knowledge_preparation
        self.knowledge_preparation.finalizers['work_turn'] = self.work_runs.finalize_knowledge
        self.knowledge_preparation.terminal_callbacks['work_turn'] = self.work_runs.release_knowledge
        self.knowledge_preparation.dependency_guards.update(
            {
                'chat': self.admission.check_knowledge_dependencies,
                'agent_preview': self.agent_runs.check_knowledge_dependencies,
                'agent_invocation': self.agent_runs.check_knowledge_dependencies,
                'work_turn': self.work_runs.check_knowledge_dependencies,
            }
        )
        self.graph = GraphService(self.knowledge)
        self.graph_store = GraphStore(self.graph)
        self.graph_runs = GraphRuns(
            self.database, self.graph_store, self.models, self.runs, self.usage, settings.token_limit
        )
        self.graph_sdk = GraphSDK(
            database_url=settings.graph_database_url.get_secret_value() if settings.graph_database_url else None,
            milvus_uri=settings.milvus_uri,
            milvus_token=settings.milvus_token.get_secret_value(),
            milvus_database=settings.milvus_database,
            working_root=settings.graph_working_root,
        )
        self.graph_jobs = GraphJobs(self.database, self.graph_store, self.graph_runs, self.graph_sdk)
        self.outbox = OutboxStore()
        self.public_runs = PublicRunService(self.keys, self.models, self.usage, self.runs)

    async def prepare_provider(self, binding: ModelBinding) -> ModelProvider:
        async with self.database.sessions() as session:
            return await self.models.prepare_provider(session, binding)

    async def require_model_execution(self, claim: ClaimedRun) -> None:
        if claim.resource_type == 'knowledge_graph':
            await self.graph_runs.guard(claim)
            return
        if claim.resource_type != 'knowledge_embedding':
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
            else:
                owner_id, expected, file_id, sha256 = await self.knowledge_jobs.store.model_guard(
                    session, claim.resource_id, claim.id
                )
                owner = await self.identity.principal_for_user(session, owner_id)
                binding = await self.models.resolve(session, expected.model_id)
                source = await self.files.require(session, owner, file_id, active=True)
                if binding_fingerprint(binding) != binding_fingerprint(expected) or source.sha256 != sha256:
                    raise AppError('knowledge_preparation_stale', '知识模型或来源已变化', 409)

    async def close(self) -> None:
        try:
            await self.graph_sdk.close()
            await self.redis.aclose()
        finally:
            await self.database.close()
