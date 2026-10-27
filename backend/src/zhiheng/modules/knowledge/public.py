from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.knowledge.adapters.milvus import MilvusStore, VectorSpec
from zhiheng.modules.knowledge.adapters.milvus_rpc import VectorIOControl
from zhiheng.modules.knowledge.adapters.native import NativeParser
from zhiheng.modules.knowledge.build import BuildClaim, BuildStore
from zhiheng.modules.knowledge.chunks import split_chunks, validate_vectors
from zhiheng.modules.knowledge.contracts import (
    JOB_TERMINAL,
    NATIVE_FORMATS,
    PARSER_VERSION,
    PREPARATION_TERMINAL,
    BaseAccess,
    BaseAcl,
    BaseAclInfo,
    BaseCreate,
    BaseInfo,
    BasePatch,
    ChunkParameters,
    DocumentImport,
    DocumentInfo,
    DocumentPage,
    DocumentResult,
    FolderCreate,
    FolderInfo,
    FolderPatch,
    Hit,
    Input,
    JobInfo,
    JobRequest,
    ParsePort,
    ParseResult,
    PreparationInfo,
    QueryRequest,
    RechunkRequest,
    Version,
)
from zhiheng.modules.knowledge.generations import GenerationStore
from zhiheng.modules.knowledge.orm import (
    GenerationPin,
    IndexGeneration,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeFolder,
    KnowledgeGrant,
    KnowledgeJob,
    KnowledgeStep,
    KnowledgeUpload,
    RetrievalIntent,
)
from zhiheng.modules.knowledge.retrieval import (
    PreparedKnowledge,
    RetrievalAdmission,
    RetrievalBase,
    RetrievalState,
    RetrievalStore,
)
from zhiheng.modules.knowledge.service import BuildSelection, KnowledgeService, binding_fingerprint, fingerprint

__all__ = [
    'JOB_TERMINAL',
    'NATIVE_FORMATS',
    'PARSER_VERSION',
    'PREPARATION_TERMINAL',
    'BaseAccess',
    'BaseAcl',
    'BaseAclInfo',
    'BaseCreate',
    'BaseInfo',
    'BasePatch',
    'BuildClaim',
    'BuildSelection',
    'BuildStore',
    'ChunkParameters',
    'DocumentImport',
    'DocumentInfo',
    'DocumentPage',
    'DocumentResult',
    'FolderCreate',
    'FolderInfo',
    'FolderPatch',
    'GenerationStore',
    'Hit',
    'Input',
    'JobInfo',
    'JobRequest',
    'KnowledgeService',
    'MilvusStore',
    'NativeParser',
    'ParsePort',
    'ParseResult',
    'PreparationInfo',
    'PreparedKnowledge',
    'QueryRequest',
    'RechunkRequest',
    'RetrievalAdmission',
    'RetrievalBase',
    'RetrievalState',
    'RetrievalStore',
    'VectorIOControl',
    'VectorSpec',
    'Version',
    'binding_fingerprint',
    'fingerprint',
    'schema_tables',
    'split_chunks',
    'validate_vectors',
]


def schema_tables() -> tuple[Table, ...]:
    return tuple(
        Base.metadata.tables[model.__tablename__]
        for model in (
            KnowledgeBase,
            KnowledgeGrant,
            KnowledgeFolder,
            KnowledgeUpload,
            KnowledgeDocument,
            IndexGeneration,
            KnowledgeChunk,
            KnowledgeJob,
            RetrievalIntent,
            KnowledgeStep,
            GenerationPin,
        )
    )
