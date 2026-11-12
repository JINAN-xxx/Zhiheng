"""Migration-only declaration inventory; excluded from business public contracts."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.knowledge.graph.orm import (
    GraphAttempt,
    GraphConfiguration,
    GraphGeneration,
    GraphJob,
    GraphPin,
    GraphStep,
)
from zhiheng.modules.knowledge.orm import (
    GenerationPin,
    IndexGeneration,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDefaults,
    KnowledgeDocument,
    KnowledgeFolder,
    KnowledgeGrant,
    KnowledgeInvitation,
    KnowledgeJob,
    KnowledgeMaterial,
    KnowledgeMigration,
    KnowledgeStep,
    KnowledgeUpload,
    KnowledgeUploadReceipt,
    RetrievalIntent,
)
from zhiheng.modules.knowledge.parsing_orm import ParsingConfiguration, ParsingJob


def schema_tables() -> tuple[Table, ...]:
    return tuple(
        Base.metadata.tables[model.__tablename__]
        for model in (
            ParsingConfiguration,
            ParsingJob,
            GraphConfiguration,
            GraphJob,
            GraphAttempt,
            GraphGeneration,
            GraphStep,
            GraphPin,
            KnowledgeBase,
            KnowledgeGrant,
            KnowledgeFolder,
            KnowledgeUpload,
            KnowledgeUploadReceipt,
            KnowledgeDocument,
            KnowledgeDefaults,
            KnowledgeMaterial,
            KnowledgeMigration,
            IndexGeneration,
            KnowledgeChunk,
            KnowledgeJob,
            KnowledgeInvitation,
            RetrievalIntent,
            KnowledgeStep,
            GenerationPin,
        )
    )
