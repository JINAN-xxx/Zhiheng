"""Library owners see actionable own references and only counts for private consumers."""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.developer.public import ResourceGrantService
from zhiheng.modules.work.public import WorkService
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from zhiheng.shared.references import ResourceReference


class KnowledgeDependencies:
    def __init__(self, agents: AgentService, work: WorkService, applications: ResourceGrantService) -> None:
        self.agents, self.work, self.applications = agents, work, applications

    async def inspect(self, session: AsyncSession, principal: Principal, base_id: UUID) -> dict[str, object]:
        rows = [
            *await self.agents.knowledge_references(session, base_id),
            *await self.work.knowledge_references(session, base_id),
            *await self.applications.knowledge_references(session, base_id),
        ]
        unique: dict[tuple[str, UUID], ResourceReference] = {}
        for row in rows:
            key = (row.kind, row.id)
            if key not in unique or row.status == 'published':
                unique[key] = row
        visible: list[ResourceReference] = []
        hidden = 0
        for row in unique.values():
            if row.owner_id == principal.id:
                visible.append(row)
            else:
                hidden += 1
        return {'items': visible, 'hidden_count': hidden}
