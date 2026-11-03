from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.agents.context import KnowledgeContext, KnowledgeContextProvider, knowledge_context
from zhiheng.modules.agents.contracts import (
    AgentConfig,
    AgentInfo,
    AgentPage,
    AgentPublicRequest,
    AgentSummary,
    AgentUpdate,
    PreviewRequest,
    VersionRequest,
)
from zhiheng.modules.agents.history import select_agent_history
from zhiheng.modules.agents.orm import Agent, AgentRunAudit, AgentVersion
from zhiheng.modules.agents.service import AgentService

__all__ = [
    'AgentConfig',
    'AgentInfo',
    'AgentPage',
    'AgentPublicRequest',
    'AgentService',
    'AgentSummary',
    'AgentUpdate',
    'KnowledgeContext',
    'KnowledgeContextProvider',
    'PreviewRequest',
    'VersionRequest',
    'knowledge_context',
    'schema_tables',
    'select_agent_history',
]


def schema_tables() -> tuple[Table, ...]:
    return tuple(Base.metadata.tables[row.__tablename__] for row in (Agent, AgentVersion, AgentRunAudit))
