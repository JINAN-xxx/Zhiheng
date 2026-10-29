from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.chat.actions.public import (
    CreatedShareInfo,
    FeedbackInfo,
    FeedbackRequest,
    FeedbackValue,
    MessageActionService,
    PublicSharedMessage,
    ShareCreateRequest,
    ShareInfo,
    SharePreview,
)
from zhiheng.modules.chat.actions.public import (
    schema_tables as action_schema_tables,
)
from zhiheng.modules.chat.contracts import (
    ChatGenerationConfig,
    ChatGenerationSettings,
    ChatRequestContext,
    ConversationInfo,
    MessageInfo,
    RegenerationTarget,
    SkillSnapshot,
)
from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.modules.chat.service import ChatService

__all__ = [
    'ChatGenerationConfig',
    'ChatGenerationSettings',
    'ChatRequestContext',
    'ChatService',
    'ConversationInfo',
    'CreatedShareInfo',
    'FeedbackInfo',
    'FeedbackRequest',
    'FeedbackValue',
    'MessageActionService',
    'MessageInfo',
    'PublicSharedMessage',
    'RegenerationTarget',
    'ShareCreateRequest',
    'ShareInfo',
    'SharePreview',
    'SkillSnapshot',
    'schema_tables',
]


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return (
        Base.metadata.tables[Conversation.__tablename__],
        Base.metadata.tables[ChatMessage.__tablename__],
        *action_schema_tables(),
    )
