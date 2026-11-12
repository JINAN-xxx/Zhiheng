"""Migration-only declaration inventory; excluded from business public contracts."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.operations.orm import Config, Notice, OperationLog


def schema_tables() -> tuple[Table, ...]:
    """Declaration inventory for explicit migrations; application startup never creates tables."""
    return tuple(Base.metadata.tables[model.__tablename__] for model in (Config, Notice, OperationLog))
