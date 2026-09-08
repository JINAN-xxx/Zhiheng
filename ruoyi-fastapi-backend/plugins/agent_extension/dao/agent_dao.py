from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from plugins.agent_extension.entity.do.agent_do import AgentConfiguration
from plugins.agent_extension.entity.vo.agent_vo import AgentPageQueryModel
from utils.page_util import PageUtil

MUTABLE_FIELDS = frozenset(
    {
        'name',
        'description',
        'model_id',
        'system_prompt',
        'welcome_message',
        'skill_slugs',
        'knowledge_base_ids',
        'application_id',
        'temperature',
        'max_tokens',
        'status',
        'published_config',
        'published_version',
        'published_at',
        'updated_at',
    }
)


class AgentDao:
    @classmethod
    async def create_agent(cls, db: AsyncSession, agent: AgentConfiguration) -> AgentConfiguration:
        db.add(agent)
        await db.flush()
        return agent

    @classmethod
    async def get_by_id(cls, db: AsyncSession, agent_id: str, *, for_update: bool = False) -> AgentConfiguration | None:
        statement = select(AgentConfiguration).where(AgentConfiguration.agent_id == agent_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return (await db.execute(statement)).scalars().first()

    @classmethod
    async def list_agents(
        cls,
        db: AsyncSession,
        query: AgentPageQueryModel,
        *,
        owner_user_id: int | None,
        is_page: bool,
    ) -> PageModel | list[dict[str, Any]]:
        statement = (
            select(AgentConfiguration)
            .where(
                AgentConfiguration.owner_user_id == owner_user_id if owner_user_id is not None else True,
                or_(
                    AgentConfiguration.name.contains(query.keyword, autoescape=True),
                    AgentConfiguration.description.contains(query.keyword, autoescape=True),
                )
                if query.keyword
                else True,
            )
            .order_by(AgentConfiguration.updated_at.desc(), AgentConfiguration.agent_id)
        )
        return await PageUtil.paginate(db, statement, query.page_num, query.page_size, is_page)

    @classmethod
    async def update_with_version(
        cls,
        db: AsyncSession,
        agent_id: str,
        *,
        expected_version: int,
        values: dict[str, Any],
    ) -> int:
        if values.keys() - MUTABLE_FIELDS:
            raise ValueError('Unsupported agent update fields')
        result = await db.execute(
            update(AgentConfiguration)
            .where(AgentConfiguration.agent_id == agent_id, AgentConfiguration.version == expected_version)
            .values(**values, version=expected_version + 1)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def delete_draft(cls, db: AsyncSession, agent_id: str, *, expected_version: int) -> int:
        result = await db.execute(
            delete(AgentConfiguration).where(
                AgentConfiguration.agent_id == agent_id,
                AgentConfiguration.status == 'draft',
                AgentConfiguration.version == expected_version,
            )
        )
        return int(result.rowcount or 0)
