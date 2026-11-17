"""Owner-scoped assistant activity comes from accepted Run snapshots and current enablement."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.orm import Agent, AgentRunAudit
from zhiheng.modules.overview.public import WorkbenchResource
from zhiheng.runtime.orm import Run
from zhiheng.shared.principal import Principal


class WorkbenchAgents:
    async def recent(
        self, session: AsyncSession, actor: Principal, additional_uses: list[tuple[UUID, str, datetime]] | None = None
    ) -> list[WorkbenchResource]:
        if not actor.has_any_permission('platform:agents:read'):
            return []
        latest = (
            select(
                AgentRunAudit.agent_id.label('agent_id'),
                func.coalesce(AgentRunAudit.config['name'].astext, '智能助手').label('name'),
                Run.created_at.label('at'),
            )
            .join(Run, Run.id == AgentRunAudit.run_id)
            .where(AgentRunAudit.owner_id == actor.id, Run.provider_started.is_(True))
            .distinct(AgentRunAudit.agent_id)
            .order_by(AgentRunAudit.agent_id, Run.created_at.desc(), Run.id.desc())
            .subquery()
        )
        rows = await session.execute(
            select(Agent.id, latest.c.name, Agent.enabled, latest.c.at)
            .join(latest, latest.c.agent_id == Agent.id)
            .where(Agent.owner_id == actor.id, Agent.deleted_at.is_(None))
            .order_by(latest.c.at.desc(), Agent.id)
            .limit(12)
        )
        result = {
            identifier: WorkbenchResource(
                kind='agent', id=identifier, name=name, occurred_at=at, status='enabled' if enabled else 'disabled'
            )
            for identifier, name, enabled, at in rows
        }
        if additional_uses:
            states = dict(
                (
                    await session.execute(
                        select(Agent.id, Agent.enabled).where(
                            Agent.id.in_([item[0] for item in additional_uses]),
                            Agent.owner_id == actor.id,
                            Agent.deleted_at.is_(None),
                        )
                    )
                )
                .tuples()
                .all()
            )
            for identifier, name, at in additional_uses:
                if identifier in states and (identifier not in result or at > result[identifier].occurred_at):
                    result[identifier] = WorkbenchResource(
                        kind='agent',
                        id=identifier,
                        name=name,
                        occurred_at=at,
                        status='enabled' if states[identifier] else 'disabled',
                    )
        return sorted(result.values(), key=lambda item: (item.occurred_at, str(item.id)), reverse=True)[:12]

    async def activities(self, session: AsyncSession, actor: Principal) -> list[WorkbenchResource]:
        if not actor.has_any_permission('platform:agents:read'):
            return []
        rows = await session.execute(
            select(Agent.id, Agent.config, Agent.enabled_at)
            .where(Agent.owner_id == actor.id, Agent.deleted_at.is_(None), Agent.enabled_at.is_not(None))
            .order_by(Agent.enabled_at.desc(), Agent.id.desc())
            .limit(6)
        )
        return [
            WorkbenchResource(
                kind='agent',
                id=row.id,
                name=str(row.config.get('name', '智能助手')),
                occurred_at=row.enabled_at,
                status='enabled',
                action='published',
            )
            for row in rows
        ]

    async def skill_uses(self, session: AsyncSession, actor: Principal) -> list[tuple[str, datetime]]:
        if not actor.has_any_permission('platform:skills:read', 'platform:agents:read'):
            return []
        rows = await session.execute(
            select(AgentRunAudit.skills, Run.created_at)
            .join(Run, Run.id == AgentRunAudit.run_id)
            .where(AgentRunAudit.owner_id == actor.id, Run.provider_started.is_(True))
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(50)
        )
        return [
            (str(item['slug']), at)
            for skills, at in rows
            for item in skills
            if isinstance(item, dict) and isinstance(item.get('slug'), str)
        ]
