from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import func, or_, select

from zhiheng.modules.agents.contracts import AgentConfig, AgentInfo, AgentPage, AgentSummary, safe_text
from zhiheng.modules.agents.orm import Agent, AgentRunAudit, AgentVersion
from zhiheng.modules.skills.public import MAX_SELECTION_BYTES, SkillSelection
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.shared.principal import Principal


def skill_values(selection: SkillSelection) -> list[dict[str, object]]:
    return [
        {key: str(value) if isinstance(value, UUID) else value for key, value in asdict(item).items()}
        for item in selection.revisions
    ]


class AgentService:
    async def _row(self, session: AsyncSession, principal: Principal, agent_id: UUID, *, lock: bool = False) -> Agent:
        query = select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
        if not principal.is_admin:
            query = query.where(Agent.owner_id == principal.id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await session.scalar(query)
        if row is None:
            raise AppError('agent_not_found', '智能体不存在或不可用', 404)
        return row

    async def get(
        self,
        session: AsyncSession,
        principal: Principal,
        agent_id: UUID,
        *,
        lock: bool = False,
        expected_version: int | None = None,
    ) -> AgentInfo:
        row = await self._row(session, principal, agent_id, lock=lock)
        if expected_version is not None and row.version != expected_version:
            raise AppError('agent_version_conflict', '智能体已更新，请刷新后重试', 409)
        published = (
            await session.scalar(
                select(AgentVersion).where(
                    AgentVersion.agent_id == row.id, AgentVersion.version == row.published_version
                )
            )
            if row.published_version is not None
            else None
        )
        return AgentInfo.model_validate(
            {
                **row.config,
                'id': row.id,
                'owner_id': row.owner_id,
                'version': row.version,
                'status': 'published' if published else 'draft',
                'published_version': row.published_version,
                'published_config': published.config if published else None,
                'created_at': row.created_at,
                'updated_at': row.updated_at,
            }
        )

    async def create(self, session: AsyncSession, principal: Principal, config: AgentConfig) -> AgentInfo:
        row = Agent(owner_id=principal.id, config=config.model_dump(mode='json'))
        session.add(row)
        await session.flush()
        return await self.get(session, principal, row.id)

    async def update(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, config: AgentConfig, expected_version: int
    ) -> AgentInfo:
        await self.get(session, principal, agent_id, lock=True, expected_version=expected_version)
        row = await self._row(session, principal, agent_id)
        row.config, row.version, row.updated_at = config.model_dump(mode='json'), row.version + 1, utcnow()
        await session.flush()
        return await self.get(session, principal, agent_id)

    async def publish(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, expected_version: int, skills: SkillSelection
    ) -> AgentInfo:
        await self.get(session, principal, agent_id, lock=True, expected_version=expected_version)
        row = await self._row(session, principal, agent_id)
        row.publication_count += 1
        row.published_version = row.publication_count
        session.add(
            AgentVersion(
                agent_id=row.id,
                version=row.publication_count,
                draft_version=row.version,
                config=row.config,
                skills=skill_values(skills),
            )
        )
        row.version, row.updated_at = row.version + 1, utcnow()
        await session.flush()
        return await self.get(session, principal, agent_id)

    async def published_selection(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, published_version: int
    ) -> SkillSelection:
        row = await self._row(session, principal, agent_id)
        if row.published_version != published_version:
            raise AppError('agent_version_conflict', '智能体已更新，请刷新后重试', 409)
        publication = await session.scalar(
            select(AgentVersion).where(AgentVersion.agent_id == agent_id, AgentVersion.version == published_version)
        )
        if publication is None:
            raise AppError('agent_publication_unavailable', '智能体发布版本不可用', 503)
        try:
            selection = TypeAdapter(SkillSelection).validate_python({'revisions': publication.skills})
            config = AgentConfig.model_validate(publication.config)
            if [item.slug for item in selection.revisions] != config.skill_slugs or sum(
                len(item.content.encode('utf-8')) for item in selection.revisions
            ) > MAX_SELECTION_BYTES:
                raise ValueError('Invalid published skill selection')
        except ValueError:
            raise AppError('agent_publication_unavailable', '智能体发布版本不可用', 503) from None
        return selection

    async def unpublish(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, expected_version: int
    ) -> AgentInfo:
        await self.get(session, principal, agent_id, lock=True, expected_version=expected_version)
        row = await self._row(session, principal, agent_id)
        row.published_version, row.version, row.updated_at = None, row.version + 1, utcnow()
        await session.flush()
        return await self.get(session, principal, agent_id)

    async def remove(self, session: AsyncSession, principal: Principal, agent_id: UUID) -> AgentInfo:
        info = await self.get(session, principal, agent_id, lock=True)
        if info.published_version is not None:
            raise AppError('agent_published', '请先取消发布智能体', 409)
        row = await self._row(session, principal, agent_id)
        row.deleted_at = row.updated_at = utcnow()
        row.version += 1
        return info.model_copy(update={'version': row.version, 'updated_at': row.updated_at})

    async def list_agents(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        page: int,
        page_size: int,
        search: str,
        status: Literal['draft', 'published'] | None,
    ) -> AgentPage:
        try:
            safe_text(search)
        except ValueError as error:
            raise AppError('invalid_agent_search', '搜索条件无效', 422) from error
        query = select(Agent.id).where(Agent.deleted_at.is_(None))
        if not principal.is_admin:
            query = query.where(Agent.owner_id == principal.id)
        if search:
            pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            query = query.where(
                or_(Agent.config['name'].astext.ilike(pattern), Agent.config['description'].astext.ilike(pattern))
            )
        if status is not None:
            query = query.where(
                Agent.published_version.is_not(None) if status == 'published' else Agent.published_version.is_(None)
            )
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        ids = await session.scalars(
            query.order_by(Agent.created_at.desc(), Agent.id).offset((page - 1) * page_size).limit(page_size)
        )
        return AgentPage(
            items=[await self.get(session, principal, agent_id) for agent_id in ids],
            total=total or 0,
            page=page,
            page_size=page_size,
        )

    async def summaries(self, session: AsyncSession, principal: Principal) -> list[AgentSummary]:
        rows = (await self.list_agents(session, principal, page=1, page_size=1000, search='', status='published')).items
        return [
            AgentSummary(
                id=row.id,
                name=row.published_config.name,
                description=row.published_config.description,
                published_version=row.published_version,
            )
            for row in rows
            if row.published_version is not None and row.published_config is not None
        ]

    async def references_model(self, session: AsyncSession, model_id: UUID) -> bool:
        return (
            await session.scalar(select(Agent.id).where(Agent.config['model_id'].astext == str(model_id)).limit(1))
        ) is not None or (
            await session.scalar(
                select(AgentVersion.id).where(AgentVersion.config['model_id'].astext == str(model_id)).limit(1)
            )
        ) is not None

    async def references_skill(self, session: AsyncSession, slug: str) -> bool:
        return (
            await session.scalar(select(Agent.id).where(Agent.config['skill_slugs'].contains([slug])).limit(1))
        ) is not None or (
            await session.scalar(
                select(AgentVersion.id).where(AgentVersion.config['skill_slugs'].contains([slug])).limit(1)
            )
        ) is not None

    async def audit(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        agent: AgentInfo,
        config: AgentConfig,
        skills: SkillSelection,
        published: bool,
        knowledge: list[dict[str, object]],
    ) -> None:
        config_version = agent.version
        if published:
            publication = await session.scalar(
                select(AgentVersion).where(
                    AgentVersion.agent_id == agent.id, AgentVersion.version == agent.published_version
                )
            )
            assert publication is not None
            config_version = publication.draft_version
        session.add(
            AgentRunAudit(
                run_id=run_id,
                agent_id=agent.id,
                owner_id=agent.owner_id,
                config_version=config_version,
                published_version=agent.published_version if published else None,
                config=config.model_dump(mode='json'),
                skills=skill_values(skills),
                knowledge=knowledge,
            )
        )
