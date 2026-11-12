"""Migration-only declaration inventory; excluded from business public contracts."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.work.orm import WorkMember, WorkMessage, WorkTask


def schema_tables() -> tuple[Table, ...]:
    return tuple(Base.metadata.tables[row.__tablename__] for row in (WorkTask, WorkMember, WorkMessage))
