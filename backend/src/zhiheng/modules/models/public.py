from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.models.contracts import (
    ConnectionInfo,
    ModelBinding,
    ModelDelta,
    ModelInfo,
    ModelMessage,
    ModelOptions,
    ModelProvider,
    ModelRequest,
    ProviderError,
    RateCreate,
    RateInfo,
)
from zhiheng.modules.models.orm import ConnectionRevision, ModelConnection, ModelDefinition, RateCard
from zhiheng.modules.models.service import ModelService

"""Normalized model contracts and model management use cases."""


__all__ = [
    'ConnectionInfo',
    'ModelBinding',
    'ModelDelta',
    'ModelInfo',
    'ModelMessage',
    'ModelOptions',
    'ModelProvider',
    'ModelRequest',
    'ModelService',
    'ProviderError',
    'RateCreate',
    'RateInfo',
    'schema_tables',
]


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return (
        Base.metadata.tables[ModelConnection.__tablename__],
        Base.metadata.tables[ModelDefinition.__tablename__],
        Base.metadata.tables[ConnectionRevision.__tablename__],
        Base.metadata.tables[RateCard.__tablename__],
    )
