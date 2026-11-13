"""Migration-only declaration inventory; excluded from business public contracts."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.chat.actions.schema import (
    schema_tables as action_schema_tables,
)
from zhiheng.modules.chat.context_orm import ContextSnapshot, ContextState, SummaryTask
from zhiheng.modules.chat.orm import ChatMessage, Conversation


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return (
        Base.metadata.tables[Conversation.__tablename__],
        Base.metadata.tables[ChatMessage.__tablename__],
        Base.metadata.tables[ContextState.__tablename__],
        Base.metadata.tables[SummaryTask.__tablename__],
        Base.metadata.tables[ContextSnapshot.__tablename__],
        *action_schema_tables(),
    )
