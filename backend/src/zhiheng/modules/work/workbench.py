"""Owner-scoped use facts from immutable accepted Work message audits."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.work.orm import WorkMessage, WorkTask
from zhiheng.runtime.orm import Run
from zhiheng.shared.principal import Principal


class WorkbenchWork:
    async def recent_agents(self, session: AsyncSession, actor: Principal) -> list[tuple[UUID, str, datetime]]:
        if not actor.has_any_permission('platform:agents:read'):
            return []
        latest = (
            select(
                WorkMessage.agent_id.label('agent_id'),
                func.coalesce(
                    WorkMessage.agent_name_snapshot, WorkMessage.audit['config']['name'].astext, '智能助手'
                ).label('name'),
                Run.created_at.label('at'),
            )
            .join(WorkTask, WorkTask.id == WorkMessage.task_id)
            .join(Run, Run.id == WorkMessage.run_id)
            .where(
                WorkTask.owner_id == actor.id,
                WorkTask.deleted_at.is_(None),
                WorkMessage.role == 'assistant',
                WorkMessage.agent_id.is_not(None),
                WorkMessage.audit['agent_owner_id'].astext == str(actor.id),
                Run.owner_id == actor.id,
                Run.resource_type == 'work_turn',
                Run.resource_id == WorkTask.id,
                Run.provider_started.is_(True),
            )
            .distinct(WorkMessage.agent_id)
            .order_by(WorkMessage.agent_id, Run.created_at.desc(), Run.id.desc())
            .subquery()
        )
        rows = await session.execute(
            select(latest.c.agent_id, latest.c.name, latest.c.at)
            .order_by(latest.c.at.desc(), latest.c.agent_id)
            .limit(12)
        )
        return [(identifier, name, at) for identifier, name, at in rows]

    async def skill_uses(self, session: AsyncSession, actor: Principal) -> list[tuple[str, datetime]]:
        if not actor.has_any_permission('platform:skills:read'):
            return []
        rows = await session.execute(
            select(WorkMessage.audit['skills'], Run.created_at)
            .join(WorkTask, WorkTask.id == WorkMessage.task_id)
            .join(Run, Run.id == WorkMessage.run_id)
            .where(
                WorkTask.owner_id == actor.id,
                WorkTask.deleted_at.is_(None),
                WorkMessage.role == 'assistant',
                WorkMessage.audit['agent_owner_id'].astext == str(actor.id),
                Run.owner_id == actor.id,
                Run.resource_type == 'work_turn',
                Run.resource_id == WorkTask.id,
                Run.provider_started.is_(True),
            )
            .order_by(Run.created_at.desc(), Run.id.desc())
            .limit(50)
        )
        return [
            (str(item['slug']), at)
            for skills, at in rows
            for item in (skills if isinstance(skills, list) else [])
            if isinstance(item, dict) and isinstance(item.get('slug'), str)
        ]
