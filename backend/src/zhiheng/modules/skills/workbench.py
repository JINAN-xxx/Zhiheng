"""Current personal skill metadata and immutable upload revision activity."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.overview.public import WorkbenchResource
from zhiheng.modules.skills.orm import Skill, SkillRevision
from zhiheng.shared.principal import Principal


class WorkbenchSkills:
    async def recent(
        self, session: AsyncSession, actor: Principal, uses: list[tuple[str, datetime]]
    ) -> list[WorkbenchResource]:
        if not actor.has_any_permission('platform:skills:read') or not uses:
            return []
        times: dict[str, datetime] = {}
        for slug, at in uses:
            if slug not in times or at > times[slug]:
                times[slug] = at
        rows = await session.execute(
            select(Skill.id, Skill.slug, SkillRevision.name, Skill.status)
            .join(SkillRevision, SkillRevision.id == Skill.revision_id)
            .where(Skill.owner_id == actor.id, Skill.deleted_at.is_(None), Skill.slug.in_(times))
        )
        return [
            WorkbenchResource(kind='skill', id=identifier, name=name, occurred_at=times[slug], status=status)
            for identifier, slug, name, status in rows
        ]

    async def activities(self, session: AsyncSession, actor: Principal) -> list[WorkbenchResource]:
        if not actor.has_any_permission('platform:skills:read'):
            return []
        rows = await session.execute(
            select(Skill.id, SkillRevision.name, Skill.status, Skill.created_at)
            .join(SkillRevision, SkillRevision.id == Skill.revision_id)
            .where(Skill.owner_id == actor.id, Skill.source == 'upload', Skill.deleted_at.is_(None))
            .order_by(Skill.created_at.desc(), Skill.id.desc())
            .limit(6)
        )
        return [
            WorkbenchResource(kind='skill', id=identifier, name=name, occurred_at=at, status=status, action='created')
            for identifier, name, status, at in rows
        ]
