"""Migration-only declaration inventory; excluded from business public contracts."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.models.orm import ConnectionRevision, ModelConnection, ModelDefinition, RateCard


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return (
        Base.metadata.tables[ModelConnection.__tablename__],
        Base.metadata.tables[ModelDefinition.__tablename__],
        Base.metadata.tables[ConnectionRevision.__tablename__],
        Base.metadata.tables[RateCard.__tablename__],
    )
