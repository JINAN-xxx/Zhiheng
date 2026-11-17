"""Explicit construction of supplier adapters for process composition."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import UUID

from zhiheng.application.agents.execution import AssistantAgentFactory
from zhiheng.application.chat.agent import ChatAgentFactory, CheckpointCleanup, ModelStart, SummaryWriter
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.agents.adapters.agent import AgentProvider
from zhiheng.infrastructure.agents.adapters.checkpoints import CheckpointStore
from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.modules.chat.public import ChatExecutionSpec
from zhiheng.modules.files.adapters.mineru import unpack_result
from zhiheng.modules.files.adapters.mineru_cloud import create_mineru_client
from zhiheng.modules.files.adapters.native import NativeParser, validate_document_source
from zhiheng.modules.files.public import DocumentSourceValidator, MinerUClientFactory, MinerUResultDecoder, ParsePort
from zhiheng.modules.knowledge.adapters.lightrag.sdk import GraphSDK
from zhiheng.modules.knowledge.adapters.milvus import MilvusStore
from zhiheng.modules.knowledge.public import GraphPort, VectorPort
from zhiheng.modules.models.adapters.factory import create_provider
from zhiheng.modules.models.public import ModelProvider, ModelService
from zhiheng.shared.agent import AgentSettings


def create_parser() -> ParsePort:
    return NativeParser()


def create_vector_store(settings: Settings) -> VectorPort:
    return MilvusStore(settings.milvus_uri, settings.milvus_token.get_secret_value(), settings.milvus_database)


def create_graph(settings: Settings) -> GraphPort:
    return GraphSDK(
        database_url=settings.graph_database_url.get_secret_value() if settings.graph_database_url else None,
        milvus_uri=settings.milvus_uri,
        milvus_token=settings.milvus_token.get_secret_value(),
        milvus_database=settings.milvus_database,
        working_root=settings.graph_working_root,
    )


def create_models(cipher: CredentialCipher, timeout_seconds: float) -> ModelService:
    return ModelService(cipher, timeout_seconds, provider_factory=create_provider)


def create_document_parsing_support() -> tuple[MinerUClientFactory, DocumentSourceValidator, MinerUResultDecoder]:
    return create_mineru_client, validate_document_source, unpack_result


def create_chat_agent_factory(settings: Settings) -> ChatAgentFactory:
    checkpoints = CheckpointStore(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def factory(
        provider: ModelProvider,
        conversation_id: UUID,
        metadata: ChatExecutionSpec,
        summarizer: SummaryWriter,
        on_model_start: ModelStart,
    ) -> AsyncIterator[ModelProvider]:
        async with checkpoints.saver() as saver:
            yield AgentProvider.from_provider(
                provider,
                checkpointer=saver,
                thread_id=str(conversation_id),
                base_checkpoint_id=metadata.base_checkpoint_id,
                message_ids=metadata.message_ids,
                incremental=metadata.incremental,
                trigger_tokens=metadata.trigger_tokens,
                keep_tokens=metadata.keep_tokens,
                summary_output_tokens=metadata.summary_output_tokens,
                input_budget=metadata.input_budget,
                summarizer=summarizer,
                on_model_start=on_model_start,
            )

    return factory


def create_chat_checkpoint_cleanup(settings: Settings) -> CheckpointCleanup:
    checkpoints = CheckpointStore(settings.database_url.get_secret_value())
    return checkpoints.delete_thread


def create_assistant_agent_factory(settings: Settings) -> AssistantAgentFactory:
    checkpoints = CheckpointStore(settings.database_url.get_secret_value(), 'assistant_checkpoints')

    @asynccontextmanager
    async def factory(
        provider: ModelProvider,
        thread_id: str,
        settings: AgentSettings,
        summarizer: SummaryWriter | None,
        on_model_start: ModelStart,
        *,
        persistent: bool,
    ) -> AsyncIterator[ModelProvider]:
        def agent(saver: object) -> AgentProvider:
            return AgentProvider.from_provider(
                provider,
                checkpointer=saver,
                thread_id=thread_id,
                base_checkpoint_id=settings.base_checkpoint_id,
                message_ids=settings.message_ids,
                incremental=settings.incremental,
                trigger_tokens=settings.trigger_tokens,
                keep_tokens=settings.keep_tokens,
                summary_output_tokens=settings.summary_output_tokens,
                input_budget=settings.input_budget,
                summarizer=summarizer,
                on_model_start=on_model_start,
            )

        if persistent:
            async with checkpoints.saver() as saver:
                yield agent(saver)
        else:
            yield agent(None)

    return factory


def create_assistant_checkpoint_cleanup(settings: Settings) -> Callable[[str], Awaitable[None]]:
    return CheckpointStore(settings.database_url.get_secret_value(), 'assistant_checkpoints').delete_thread
