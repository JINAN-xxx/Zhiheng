from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.chat.contracts import ConversationInfo, MessageInfo
from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.modules.chat.service import ChatService

__all__ = ['ChatService', 'ConversationInfo', 'MessageInfo', 'schema_tables']


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return (Base.metadata.tables[Conversation.__tablename__], Base.metadata.tables[ChatMessage.__tablename__])
