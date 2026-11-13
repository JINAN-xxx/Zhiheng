"""Execution snapshots inherit their resource's personal content boundary."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import AgentService, AssistantConversations
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.shared.platform_permissions import require_capability
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from zhiheng.application.knowledge.graph.runs import GraphRuns
    from zhiheng.application.knowledge.preparation import KnowledgePreparation


class RunContentAccess:
    def __init__(
        self, agents: AgentService, work: WorkService, graph: 'GraphRuns', knowledge: 'KnowledgePreparation'
    ) -> None:
        self.agents, self.work, self.graph, self.knowledge = agents, work, graph, knowledge

    async def require_personal(self, session: AsyncSession, principal: Principal, kind: str, resource_id: UUID) -> None:
        if kind in {'chat', 'chat_summary'}:
            require_capability(principal, 'chat')
        elif kind == 'agent_chat':
            require_capability(principal, 'agents', 'access')
        elif kind == 'agent_preview':
            require_capability(principal, 'agents', 'read')
        elif kind == 'work_turn':
            require_capability(principal, 'work', 'read')
        if kind in {'agent_preview', 'agent_invocation'}:
            await self.agents.get(session, principal, resource_id)
        elif kind == 'agent_chat':
            await AssistantConversations().authorize(session, principal, resource_id)
        elif kind == 'work_turn':
            await self.work.require(session, principal, resource_id)

    async def authorize_read(self, session: AsyncSession, principal: Principal, value: RunSnapshot) -> None:
        await self.require_personal(session, principal, value.resource_type, value.resource_id)
        if value.resource_type in {'public', 'agent_invocation'}:
            require_capability(principal, 'developer', 'read')
        elif value.resource_type == 'connection_test':
            principal.require_permission('ai:models:test')
        elif value.resource_type in {'knowledge_embedding', 'knowledge_rerank'}:
            require_capability(principal, 'knowledge', 'read')
            state = await self.knowledge.store.find_state(session, value.resource_id)
            if state is not None:
                module = {'chat': 'chat', 'agent_chat': 'agents', 'agent_preview': 'agents', 'work_turn': 'work'}.get(
                    state.resource_type
                )
                if module is not None:
                    require_capability(principal, module, 'read')
                if state.key_id is not None:
                    require_capability(principal, 'developer', 'read')
        await self.graph.authorize_read(session, principal, value)
