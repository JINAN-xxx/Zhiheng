from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.chat.actions.contracts import (
    CreatedShareInfo,
    FeedbackInfo,
    FeedbackRequest,
    FeedbackValue,
    PublicSharedMessage,
    ShareCreateRequest,
    ShareInfo,
    SharePreview,
)
from zhiheng.modules.chat.actions.orm import MessageFeedback, MessageShare
from zhiheng.modules.chat.actions.service import MessageActionService

__all__ = [
    'CreatedShareInfo',
    'FeedbackInfo',
    'FeedbackRequest',
    'FeedbackValue',
    'MessageActionService',
    'PublicSharedMessage',
    'ShareCreateRequest',
    'ShareInfo',
    'SharePreview',
    'schema_tables',
]


def schema_tables() -> tuple[Table, ...]:
    return (Base.metadata.tables[MessageFeedback.__tablename__], Base.metadata.tables[MessageShare.__tablename__])
