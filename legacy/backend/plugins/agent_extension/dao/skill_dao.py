from datetime import datetime
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from plugins.agent_extension.entity.do.skill_do import AgentSkill
from plugins.agent_extension.entity.vo.skill_vo import SkillPageQueryModel
from utils.page_util import PageUtil


class SkillDao:
    @classmethod
    async def create_skill(cls, db: AsyncSession, skill: AgentSkill) -> AgentSkill:
        db.add(skill)
        await db.flush()
        return skill

    @classmethod
    async def get_by_id(cls, db: AsyncSession, skill_id: str) -> AgentSkill | None:
        return (await db.execute(select(AgentSkill).where(AgentSkill.skill_id == skill_id))).scalars().first()

    @classmethod
    async def get_by_id_for_update(cls, db: AsyncSession, skill_id: str) -> AgentSkill | None:
        return (
            (
                await db.execute(
                    select(AgentSkill)
                    .where(AgentSkill.skill_id == skill_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_by_slug(cls, db: AsyncSession, slug: str) -> AgentSkill | None:
        return (await db.execute(select(AgentSkill).where(AgentSkill.skill_slug == slug))).scalars().first()

    @classmethod
    async def list_skills(
        cls,
        db: AsyncSession,
        query: SkillPageQueryModel,
        *,
        is_page: bool,
    ) -> PageModel | list[dict[str, Any]]:
        statement = (
            select(AgentSkill)
            .where(
                or_(
                    AgentSkill.skill_name.contains(query.keyword, autoescape=True),
                    AgentSkill.skill_slug.contains(query.keyword, autoescape=True),
                    AgentSkill.description.contains(query.keyword, autoescape=True),
                )
                if query.keyword
                else True,
                AgentSkill.status == query.status if query.status else True,
            )
            .order_by(AgentSkill.source_type, AgentSkill.skill_name, AgentSkill.skill_slug)
        )
        return await PageUtil.paginate(db, statement, query.page_num, query.page_size, is_page)

    @classmethod
    async def list_available(cls, db: AsyncSession) -> list[AgentSkill]:
        result = await db.execute(
            select(AgentSkill)
            .where(AgentSkill.status == 'enabled')
            .order_by(AgentSkill.skill_name, AgentSkill.skill_slug)
        )
        return list(result.scalars().all())

    @classmethod
    async def get_by_slugs(cls, db: AsyncSession, slugs: list[str]) -> list[AgentSkill]:
        if not slugs:
            return []
        result = await db.execute(select(AgentSkill).where(AgentSkill.skill_slug.in_(slugs)))
        return list(result.scalars().all())

    @classmethod
    async def update_status_with_version(
        cls,
        db: AsyncSession,
        skill_id: str,
        *,
        expected_version: int,
        status: str,
        operator: str,
        now: datetime,
    ) -> int:
        result = await db.execute(
            update(AgentSkill)
            .where(AgentSkill.skill_id == skill_id, AgentSkill.lock_version == expected_version)
            .values(
                status=status,
                lock_version=expected_version + 1,
                updated_by=operator,
                updated_at=now,
            )
        )
        return int(result.rowcount or 0)

    @classmethod
    async def delete_disabled_upload(cls, db: AsyncSession, skill_id: str) -> int:
        result = await db.execute(
            delete(AgentSkill).where(
                AgentSkill.skill_id == skill_id,
                AgentSkill.status == 'disabled',
                AgentSkill.source_type == 'upload',
            )
        )
        return int(result.rowcount or 0)
