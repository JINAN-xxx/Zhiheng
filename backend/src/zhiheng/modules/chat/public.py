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
from zhiheng.modules.chat.context_orm import ContextSnapshot, ContextState, SummaryTask
from zhiheng.modules.chat.context_policy import (
    SUMMARY_KEEP,
    SUMMARY_OUTPUT_TOKENS,
    ContextPackage,
    ContextTurn,
    attachment_refs,
    build_context,
    estimate_messages,
    input_budget_for,
    plan_summary,
    source_fingerprint,
    source_refs,
    summary_message,
)
from zhiheng.modules.chat.context_store import ContextStore
from zhiheng.modules.chat.contracts import (
    ChatContextInfo,
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
    'SUMMARY_KEEP',
    'SUMMARY_OUTPUT_TOKENS',
    'ChatContextInfo',
    'ChatGenerationConfig',
    'ChatGenerationSettings',
    'ChatRequestContext',
    'ChatService',
    'ContextPackage',
    'ContextSnapshot',
    'ContextState',
    'ContextStore',
    'ContextTurn',
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
    'SummaryTask',
    'attachment_refs',
    'build_context',
    'estimate_messages',
    'input_budget_for',
    'plan_summary',
    'schema_tables',
    'source_fingerprint',
    'source_refs',
    'summary_message',
]


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
