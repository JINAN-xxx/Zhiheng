from redis.asyncio import Redis

from zhiheng.application.access import ResourceAccessService
from zhiheng.application.agents.management import AgentManagementService
from zhiheng.application.agents.preparations import AgentPreparations
from zhiheng.application.agents.runs import AgentRunService
from zhiheng.application.chat.context import ChatContextConfig, ChatContextService
from zhiheng.application.chat.runs import ChatRunService
from zhiheng.application.developer.management import DeveloperManagementService
from zhiheng.application.developer.resources import DeveloperResourceQueries
from zhiheng.application.execution.admission import RunAdmission
from zhiheng.application.execution.authorization import ModelExecutionAuthorization
from zhiheng.application.execution.content_access import RunContentAccess
from zhiheng.application.execution.executor import InferenceExecutor
from zhiheng.application.execution.lifecycle import RunLifecycle
from zhiheng.application.execution.public_runs import PublicRunService
from zhiheng.application.execution.recovery import RunRecovery
from zhiheng.application.execution.references import RunReferences
from zhiheng.application.files.commands import FileCommands
from zhiheng.application.files.enrollments import FileEnrollmentRelease
from zhiheng.application.files.inputs import FileInputService
from zhiheng.application.files.objects import FileObjects
from zhiheng.application.files.parsing import DocumentParsing
from zhiheng.application.files.queries import FileQueries
from zhiheng.application.identity.accounts import AccountService
from zhiheng.application.knowledge.commands import KnowledgeCommands
from zhiheng.application.knowledge.dependencies import KnowledgeDependencies
from zhiheng.application.knowledge.documents import KnowledgeDocuments
from zhiheng.application.knowledge.graph.jobs import GraphJobs
from zhiheng.application.knowledge.graph.runs import GraphRuns
from zhiheng.application.knowledge.jobs import KnowledgeJobs
from zhiheng.application.knowledge.origin import KnowledgeOriginGuard
from zhiheng.application.knowledge.preparation import KnowledgePreparation
from zhiheng.application.knowledge.queries import KnowledgeQueries
from zhiheng.application.knowledge.settings import KnowledgeConfiguration
from zhiheng.application.models.configuration import ModelConfigurationService
from zhiheng.application.models.management import ModelManagementService
from zhiheng.application.operations.cancellation import OperationCancellation
from zhiheng.application.operations.queries import OperationQueries
from zhiheng.application.skills.commands import SkillCommands
from zhiheng.application.skills.management import PreparationSkillOwners, SkillManagementService
from zhiheng.application.skills.queries import SkillQueries
from zhiheng.application.work.runs import WorkRunService
from zhiheng.bootstrap.adapters import (
    create_document_parsing_support,
    create_graph,
    create_models,
    create_parser,
    create_vector_store,
)
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.infrastructure.database import Database
from zhiheng.infrastructure.file_storage import LocalFileStorage
from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.billing.public import CreditService, MeteringService, UsageFactsService, UsageService
from zhiheng.modules.chat.public import ChatService, MessageActionService
from zhiheng.modules.developer.public import (
    APIRequestService,
    ApplicationService,
    KeyMeteringPolicy,
    KeyService,
    ResourceGrantService,
    ResourceLimitService,
)
from zhiheng.modules.files.public import FileJobs, FileService
from zhiheng.modules.identity.public import IdentityAdminService, IdentityService
from zhiheng.modules.knowledge.public import (
    GraphService,
    GraphStore,
    KnowledgeDefaultsService,
    KnowledgeService,
    KnowledgeSharing,
    ParsingSettingsService,
)
from zhiheng.modules.operations.public import OperationsService
from zhiheng.modules.skills.public import SkillService
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.outbox import OutboxStore
from zhiheng.runtime.runs import RunStore


class Container:
    def __init__(self, settings: Settings) -> None:  # noqa: PLR0915 - explicit process composition

        self.settings = settings
        self.database = Database(settings.database_url.get_secret_value())
        self.redis = Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)
        self.cipher = CredentialCipher(settings.credential_key.get_secret_value())
        self.identity, self.skills = IdentityService(settings.session_ttl_seconds), SkillService()
        self.admin_identity = IdentityAdminService(self.identity)
        self.operations = OperationsService()
        self.files = FileService(LocalFileStorage(settings.files_root), self.identity)
        self.file_objects = FileObjects(self.database, self.files)
        self.file_jobs = FileJobs(self.files, self.database.sessions)
        self.file_inputs = FileInputService(self.database, self.files, create_parser(), self.file_objects)
        self.parsing_settings = ParsingSettingsService(self.cipher)
        parsing_client, parsing_validator, parsing_decoder = create_document_parsing_support()
        self.document_parsing = DocumentParsing(
            self.database,
            self.files,
            self.file_inputs,
            self.identity,
            self.parsing_settings,
            parsing_client,
            parsing_validator,
            parsing_decoder,
        )
        self.models = create_models(self.cipher, settings.provider_timeout_seconds)
        self.model_configuration = ModelConfigurationService(self.database, self.models, self.identity)
        self.agents, self.work = AgentService(), WorkService()
        self.knowledge = KnowledgeService(
            self.files,
            self.identity,
            self.models,
            preparation_skill_owner=PreparationSkillOwners(self.agents, self.work).resolve,
        )
        self.files.library_access = self.knowledge.authorize_library_file
        self.files.library_locks = self.knowledge.lock_library_sources
        self.knowledge_sharing = KnowledgeSharing(
            self.knowledge, self.identity, settings.credential_key.get_secret_value().encode()
        )
        self.knowledge_defaults = KnowledgeDefaultsService(self.models)
        self.knowledge_configuration = KnowledgeConfiguration(self.database, self.knowledge, self.models)
        self.credits = CreditService()
        self.usage_facts = UsageFactsService()
        self.key_metering = KeyMeteringPolicy(self.usage_facts, self.identity)
        self.metering = MeteringService(self.key_metering)
        self.usage = UsageService(self.credits, self.metering)
        self.applications = ApplicationService()
        self.keys = KeyService(self.applications, self.models, self.identity, self.usage_facts)
        self._configure_resource_access()
        self.developer_management = DeveloperManagementService(self.applications, self.keys, self.models)
        self.runs = RunStore()
        self.run_references = RunReferences(self.runs)
        self.work_runs = WorkRunService(self.work, self.agents, self.models, self.identity, self.usage, self.runs)
        self.model_management = ModelManagementService(
            self.models, self.run_references, self.keys, self.agents, knowledge=self.knowledge
        )
        self.accounts = AccountService(self.identity, self.skills, self.admin_identity)
        self.agent_management = AgentManagementService(
            self.agents, self.applications, self.models, self.work, self.run_references, self.knowledge
        )
        self.agent_runs = AgentRunService(
            self.agents, self.agent_management, self.keys, self.models, self.usage, self.runs
        )
        self.chat = ChatService()
        self.run_lifecycle = RunLifecycle(self.chat, self.work)
        self.run_admission = RunAdmission(self.models, self.usage, self.runs)
        self.run_recovery = RunRecovery(self.database.sessions, self.runs, self.usage, self.run_lifecycle)
        self.skill_management = SkillManagementService(
            self.skills, self.agents, self.work, self.knowledge, chat=self.chat
        )
        self.message_actions, self.admission = (
            MessageActionService(self.chat, self.identity),
            ChatRunService(self.chat, self.models, self.usage, self.runs, self.files, admission=self.run_admission),
        )
        self.chat_context = ChatContextService(
            self.database,
            self.chat,
            self.models,
            self.identity,
            self.files,
            self.run_admission,
            settings.token_limit,
            runs=self.runs,
            usage=self.usage,
            config=ChatContextConfig(
                settings.chat_context_input_tokens,
                settings.chat_summary_trigger_rounds,
                settings.chat_summary_keep_rounds,
                settings.chat_summary_output_tokens,
            ),
        )
        self.admission.context = self.chat_context
        self.vectors = create_vector_store(settings)
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
        self._configure_resource_facades()
        self.agent_preparations = AgentPreparations(self.database, self.keys, self.agents, self.knowledge_preparation)
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
        self.run_content = RunContentAccess(self.agents, self.work, self.graph_runs)
        self.graph_sdk = create_graph(settings)
        self.graph_jobs = GraphJobs(self.database, self.graph_store, self.graph_runs, self.graph_sdk)
        self.outbox = OutboxStore()
        self.public_runs = PublicRunService(self.keys, self.models, self.usage, self.runs)
        self.execution_authorization = ModelExecutionAuthorization(
            database=self.database,
            chat_context=self.chat_context,
            keys=self.keys,
            graph_runs=self.graph_runs,
            run_content=self.run_content,
            knowledge_preparation=self.knowledge_preparation,
            knowledge_origin=self.knowledge_origin,
            knowledge_jobs=self.knowledge_jobs,
            identity=self.identity,
            models=self.models,
            resource_access=self.resource_access,
            files=self.files,
        )
        self.run_executor = InferenceExecutor(
            sessions=self.database.sessions,
            runs=self.runs,
            usage=self.usage,
            chat=self.chat,
            provider_factory=self.execution_authorization.prepare_provider,
            lease_seconds=settings.lease_seconds,
            timeout_seconds=settings.provider_timeout_seconds,
            lifecycle=self.run_lifecycle,
            before_provider=self.execution_authorization.require,
        )

    def _configure_resource_facades(self) -> None:
        self.skill_commands = SkillCommands(
            self.database, self.skills, self.skill_management, self.resource_access, self.file_queries
        )
        self.skill_queries = SkillQueries(self.database, self.skills, self.resource_access)
        self.knowledge_origin = KnowledgeOriginGuard(self.resource_access)
        self.knowledge_jobs.origin_guard = self.knowledge_origin
        self.knowledge_jobs.document_parsing = self.document_parsing
        self.document_parsing.authorize_origin = self.knowledge_jobs.authorize_parse_origin
        self.knowledge_preparation.resource_guard = self.knowledge_origin.retrieval
        self.operation_queries = OperationQueries(
            self.database, self.resource_access, self.knowledge, self.knowledge_preparation
        )
        self.operation_cancellation = OperationCancellation(self.operation_queries)
        self.knowledge_commands = KnowledgeCommands(
            self.database,
            self.knowledge,
            self.models,
            self.usage,
            self.resource_access,
            self.knowledge_preparation,
            self.operation_queries,
        )
        self.knowledge_queries = KnowledgeQueries(self.database, self.knowledge, self.resource_access)
        self.knowledge_documents = KnowledgeDocuments(
            self.database, self.knowledge, self.files, self.file_inputs, self.file_objects
        )
        self.knowledge_queries.document_workflows = self.knowledge_documents
        self.knowledge_commands.documents = self.knowledge_documents
        self.knowledge_dependencies = KnowledgeDependencies(self.agents, self.work, self.resource_grants)
        self.knowledge.base_references = self.knowledge_dependencies.inspect

    def _configure_resource_access(self) -> None:
        self.resource_grants = ResourceGrantService(self.applications)
        self.api_requests = APIRequestService(self.keys)
        self.resource_limits = ResourceLimitService(self.keys, platform_rpm=self.settings.resource_rpm_limit)
        self.resource_access = ResourceAccessService(
            self.identity, self.keys, self.resource_grants, self.api_requests, self.files, self.knowledge, self.skills
        )

        self.developer_resources = DeveloperResourceQueries(self.applications, self.resource_access)
        self.file_commands = FileCommands(self.database, self.files, self.resource_access, self.file_objects)
        self.file_queries = FileQueries(self.database, self.files, self.resource_access)
        self.file_enrollments = FileEnrollmentRelease(self.database, self.resource_grants, self.resource_access)
        self.file_jobs.after_purge = self.file_enrollments.release

    async def close(self) -> None:
        try:
            await self.graph_sdk.close()
            await self.redis.aclose()
        finally:
            await self.database.close()
