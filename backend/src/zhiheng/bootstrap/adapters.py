"""Explicit construction of supplier adapters for process composition."""

from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.credentials import CredentialCipher
from zhiheng.modules.knowledge.adapters.lightrag.sdk import GraphSDK
from zhiheng.modules.knowledge.adapters.milvus import MilvusStore
from zhiheng.modules.knowledge.adapters.mineru import unpack_result
from zhiheng.modules.knowledge.adapters.mineru_cloud import create_mineru_client
from zhiheng.modules.knowledge.adapters.native import NativeParser, validate_document_source
from zhiheng.modules.knowledge.public import (
    DocumentSourceValidator,
    GraphPort,
    MinerUClientFactory,
    MinerUResultDecoder,
    ParsePort,
    VectorPort,
)
from zhiheng.modules.models.adapters.factory import create_provider
from zhiheng.modules.models.public import ModelService


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
