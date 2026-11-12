"""Migration-only declaration inventory; excluded from business public contracts."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.chat.actions.orm import MessageFeedback, MessageShare


def schema_tables() -> tuple[Table, ...]:
    return (Base.metadata.tables[MessageFeedback.__tablename__], Base.metadata.tables[MessageShare.__tablename__])
