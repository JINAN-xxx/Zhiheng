"""Compose owner-scoped resource facts and separately authorized platform aggregates."""

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.public import WorkbenchAgents
from zhiheng.modules.billing.public import WorkbenchBilling
from zhiheng.modules.chat.public import WorkbenchChat
from zhiheng.modules.developer.public import WorkbenchDeveloper
from zhiheng.modules.knowledge.public import WorkbenchKnowledge
from zhiheng.modules.models.public import WorkbenchModels
from zhiheng.modules.overview.public import WorkbenchAlert, WorkbenchOverview
from zhiheng.modules.skills.public import WorkbenchSkills
from zhiheng.modules.work.public import WorkbenchWork
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


class WorkbenchService:
    async def read(
        self, session: AsyncSession, actor: Principal, scope: Literal['mine', 'platform'], days: Literal[7, 30]
    ) -> WorkbenchOverview:
        actor.require_permission('platform:overview:read')
        can_platform = actor.has_any_permission('platform:overview:platform_stats')
        if scope == 'platform':
            actor.require_permission('platform:overview:platform_stats')
        now = utcnow()
        billing = WorkbenchBilling()
        usage_allowed = actor.has_any_permission('platform:usage:read', 'ai:credits:read') or scope == 'platform'
        owner_id = None if scope == 'platform' else actor.id
        metrics = await billing.metrics(session, owner_id, now, days) if usage_allowed else None
        credits_allowed = (
            actor.has_any_permission('ai:credits:read')
            if scope == 'platform'
            else actor.has_any_permission('platform:usage:read', 'ai:credits:read')
        )
        credit_summary = await billing.credits(session, owner_id, now) if credits_allowed else None
        agents, knowledge, skills = WorkbenchAgents(), WorkbenchKnowledge(), WorkbenchSkills()
        work = WorkbenchWork()
        references = (
            await billing.recent_models(session, actor.id) if actor.has_any_permission('platform:models:read') else []
        )
        recent = await WorkbenchModels().recent(session, actor, references)
        recent.extend(await agents.recent(session, actor, await work.recent_agents(session, actor)))
        recent.extend(await knowledge.recent(session, actor))
        skill_uses = await WorkbenchChat().skill_uses(session, actor)
        skill_uses.extend(await agents.skill_uses(session, actor))
        skill_uses.extend(await work.skill_uses(session, actor))
        recent.extend(await skills.recent(session, actor, skill_uses))
        activities = await agents.activities(session, actor)
        activities.extend(await knowledge.activities(session, actor))
        activities.extend(await skills.activities(session, actor))
        alerts = await knowledge.alerts(session, actor, now)
        alerts.extend(await WorkbenchDeveloper().alerts(session, actor, now))
        if actor.has_any_permission('platform:usage:read', 'ai:credits:read'):
            failures = await billing.recent_failures(session, actor.id, now)
            if failures:
                alerts.append(
                    WorkbenchAlert(
                        id='recent-failures',
                        level='error',
                        title=f'近24小时有 {failures} 次请求失败或中断',
                        description='查看调用记录可定位原因，已发生用量按实际记录保留。',
                    )
                )
        recent.sort(key=lambda item: (item.occurred_at, str(item.id)), reverse=True)
        activities.sort(key=lambda item: (item.occurred_at, str(item.id)), reverse=True)
        alerts.sort(key=lambda item: ({'error': 0, 'warning': 1, 'info': 2}[item.level], item.id))
        return WorkbenchOverview(
            scope=scope,
            can_view_platform=can_platform,
            period_days=days,
            observed_at=now,
            metrics=metrics,
            credits=credit_summary,
            recent=recent[:24],
            activities=activities[:6],
            alerts=alerts[:6],
        )
