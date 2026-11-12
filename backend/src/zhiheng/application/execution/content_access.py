"""Execution snapshots inherit their resource's personal content boundary."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.work.public import WorkService
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from zhiheng.application.knowledge.graph.runs import GraphRuns


class RunContentAccess:
    def __init__(self, agents: AgentService, work: WorkService, graph: 'GraphRuns') -> None:
        self.agents, self.work, self.graph = agents, work, graph

    async def require_personal(self, session: AsyncSession, principal: Principal, kind: str, resource_id: UUID) -> None:
        if kind in {'agent_preview', 'agent_invocation'}:
            await self.agents.get(session, principal, resource_id)
        elif kind == 'work_turn':
            await self.work.require(session, principal, resource_id)

    async def authorize_read(self, session: AsyncSession, principal: Principal, value: RunSnapshot) -> None:
        await self.require_personal(session, principal, value.resource_type, value.resource_id)
        await self.graph.authorize_read(session, principal, value)
