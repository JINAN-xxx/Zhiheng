from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import aliased

from zhiheng.modules.agents.contracts import (
    AgentConfig,
    AgentInfo,
    AgentPage,
    AgentSummary,
    PlazaAssistant,
    PlazaAssistantPage,
    PlazaConfig,
    safe_text,
)
from zhiheng.modules.agents.orm import Agent, AgentRunAudit, AssistantConversation, AssistantSummary
from zhiheng.runtime.contracts import TERMINAL_STATUSES, ClaimedRun
from zhiheng.runtime.orm import Run
from zhiheng.runtime.runs import ACTIVE_STATUSES
from zhiheng.shared.errors import AppError
from zhiheng.shared.references import ResourceReference
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import ColumnElement

    from zhiheng.modules.skills.public import SkillSelection
    from zhiheng.shared.principal import Principal


def skill_values(selection: SkillSelection) -> list[dict[str, object]]:
    return [
        {key: str(value) if isinstance(value, UUID) else value for key, value in asdict(item).items()}
        for item in selection.revisions
    ]


class AgentService:
    async def resource_owner(self, session: AsyncSession, agent_id: UUID) -> UUID | None:
        """Trusted owner metadata for application composition of persisted dependencies."""
        owner_id: UUID | None = await session.scalar(
            select(Agent.owner_id).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
        )
        return owner_id

    async def _row(self, session: AsyncSession, principal: Principal, agent_id: UUID, *, lock: bool = False) -> Agent:
        query = select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
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
        return AgentInfo.model_validate(
            {
                **row.config,
                'id': row.id,
                'owner_id': row.owner_id,
                'version': row.version,
                'status': 'enabled' if row.enabled else 'disabled',
                'created_at': row.created_at,
                'updated_at': row.updated_at,
                'plaza_version': row.plaza_version,
                'plaza_department_id': row.plaza_config.get('department_id') if row.plaza_config else None,
            }
        )

    @staticmethod
    def _published_rows() -> Select[tuple[Agent]]:
        return select(Agent).where(
            Agent.deleted_at.is_(None), Agent.plaza_config.is_not(None), Agent.plaza_version.is_not(None)
        )

    @staticmethod
    def _plaza_info(row: Agent, principal: Principal) -> PlazaAssistant:
        config = PlazaConfig.model_validate(row.plaza_config)
        assert row.plaza_version is not None
        return PlazaAssistant(
            id=row.id,
            name=config.name,
            description=config.description,
            welcome_message=config.welcome_message,
            plaza_version=row.plaza_version,
            can_start=config.includes(principal.department_id),
        )

    def _visible_plaza(self, principal: Principal) -> Select[tuple[Agent]]:
        query = self._published_rows().where(Agent.enabled)
        if principal.is_admin:
            return query
        department = Agent.plaza_config['department_id'].astext
        if principal.department_id is None:
            return query.where(department.is_(None))
        return query.where(or_(department.is_(None), department == str(principal.department_id)))

    async def plaza_detail(self, session: AsyncSession, principal: Principal, agent_id: UUID) -> PlazaAssistant:
        row = await session.scalar(self._visible_plaza(principal).where(Agent.id == agent_id))
        if row is None:
            raise AppError('assistant_not_on_plaza', '此助手未上架或已下架', 404)
        return self._plaza_info(row, principal)

    async def plaza_page(
        self, session: AsyncSession, principal: Principal, *, search: str, page: int, page_size: int
    ) -> PlazaAssistantPage:
        try:
            safe_text(search)
        except ValueError as error:
            raise AppError('invalid_agent_search', '搜索条件无效', 422) from error
        query = self._visible_plaza(principal)
        name = Agent.plaza_config['name'].astext
        if term := search.strip():
            query = query.where(
                or_(
                    name.icontains(term, autoescape=True),
                    Agent.plaza_config['description'].astext.icontains(term, autoescape=True),
                )
            )
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(query.order_by(name, Agent.id).offset((page - 1) * page_size).limit(page_size))
        return PlazaAssistantPage(items=[self._plaza_info(row, principal) for row in rows], total=total or 0)

    async def publish_plaza(
        self,
        session: AsyncSession,
        principal: Principal,
        agent_id: UUID,
        expected_version: int,
        config: PlazaConfig,
    ) -> AgentInfo:
        principal.require_permission('platform:agents:plaza_publish')
        row = await self._row(session, principal, agent_id, lock=True)
        if row.version != expected_version:
            raise AppError('agent_version_conflict', '智能体已更新，请刷新后重试', 409)
        row.version += 1
        row.updated_at = utcnow()
        row.plaza_version = row.version
        row.plaza_config = config.model_dump(mode='json')
        await session.flush()
        return await self.get(session, principal, agent_id)

    async def unpublish_plaza(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, expected_version: int
    ) -> None:
        principal.require_permission('platform:agents:plaza_publish')
        query = self._published_rows().where(Agent.id == agent_id)
        if not principal.is_admin:
            query = query.where(Agent.owner_id == principal.id)
        row = await session.scalar(query.with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise AppError('assistant_not_on_plaza', '此助手未上架或已下架', 404)
        if row.plaza_version != expected_version:
            raise AppError('assistant_plaza_version_conflict', '广场版本已更新，请刷新后重试', 409)
        row.plaza_config = row.plaza_version = None
        row.version += 1
        row.updated_at = utcnow()

    async def require_enabled_application(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, application_id: UUID, *, lock: bool = False
    ) -> AgentInfo:
        try:
            agent = await self.get(session, principal, agent_id, lock=lock)
        except AppError as error:
            if error.code != 'agent_not_found':
                raise
            raise AppError('agent_not_found', 'Agent is unavailable', 404) from None
        if not agent.enabled or agent.application_id != application_id:
            raise AppError('agent_not_found', 'Agent is unavailable', 404)
        return agent

    async def create(
        self, session: AsyncSession, principal: Principal, config: AgentConfig, *, client_request_id: UUID | None = None
    ) -> AgentInfo:
        row = Agent(
            owner_id=principal.id,
            config=config.model_dump(mode='json'),
            create_client_request_id=client_request_id,
            create_fingerprint=self.create_hash(config),
        )
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

    async def set_enabled(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, expected_version: int, enabled: bool
    ) -> AgentInfo:
        await self.get(session, principal, agent_id, lock=True, expected_version=expected_version)
        row = await self._row(session, principal, agent_id)
        row.enabled = enabled
        if enabled:
            row.enabled_at = utcnow()
        row.version += 1
        row.updated_at = utcnow()
        await session.flush()
        return await self.get(session, principal, row.id)

    async def remove(
        self,
        session: AsyncSession,
        principal: Principal,
        agent_id: UUID,
        *,
        reference_check: Callable[[AsyncSession, UUID], Awaitable[bool]] | None = None,
    ) -> AgentInfo:
        info = await self.get(session, principal, agent_id, lock=True)
        if reference_check is not None and await reference_check(session, agent_id):
            raise AppError('agent_referenced', '智能体仍被当前任务引用', 409)
        row = await self._row(session, principal, agent_id)
        row.deleted_at = row.updated_at = utcnow()
        await session.execute(
            update(AssistantConversation)
            .where(
                AssistantConversation.agent_id == agent_id,
                AssistantConversation.owner_id == principal.id,
                AssistantConversation.plaza.is_(False),
                AssistantConversation.deleted_at.is_(None),
            )
            .values(deleted_at=row.deleted_at)
        )
        row.enabled = False
        row.plaza_config = row.plaza_version = None
        row.version += 1
        return info.model_copy(
            update={
                'version': row.version,
                'updated_at': row.updated_at,
                'status': 'disabled',
                'plaza_version': None,
            }
        )

    async def list_agents(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        page: int,
        page_size: int,
        search: str,
        status: Literal['enabled', 'disabled'] | None,
    ) -> AgentPage:
        try:
            safe_text(search)
        except ValueError as error:
            raise AppError('invalid_agent_search', '搜索条件无效', 422) from error
        query = select(Agent.id).where(Agent.deleted_at.is_(None))
        query = query.where(Agent.owner_id == principal.id)
        if search:
            pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            query = query.where(
                or_(Agent.config['name'].astext.ilike(pattern), Agent.config['description'].astext.ilike(pattern))
            )
        if status is not None:
            query = query.where(Agent.enabled.is_(status == 'enabled'))
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
        rows = (await self.list_agents(session, principal, page=1, page_size=1000, search='', status='enabled')).items
        return [
            AgentSummary(
                id=row.id,
                name=row.name,
                description=row.description,
            )
            for row in rows
        ]

    async def application_candidates(
        self, session: AsyncSession, owner_id: UUID, application_id: UUID
    ) -> list[tuple[UUID, str]]:
        rows = await session.execute(
            select(Agent.id, Agent.config['name'].astext)
            .where(
                Agent.owner_id == owner_id,
                Agent.deleted_at.is_(None),
                Agent.enabled.is_(True),
                Agent.config['application_id'].astext == str(application_id),
            )
            .order_by(Agent.config['name'].astext, Agent.id)
        )
        return [(agent_id, name) for agent_id, name in rows]

    async def _configuration_references(
        self, session: AsyncSession, match: ColumnElement[bool]
    ) -> list[ResourceReference]:
        rows = await session.scalars(select(Agent).where(Agent.deleted_at.is_(None), match).order_by(Agent.id))
        return [
            ResourceReference(
                'agent',
                row.id,
                str(row.config.get('name', '智能助手')),
                'enabled' if row.enabled else 'disabled',
                row.owner_id,
                f'/agents/{row.id}',
            )
            for row in rows
        ]

    async def model_references(self, session: AsyncSession, model_id: UUID) -> list[ResourceReference]:
        return await self._configuration_references(
            session,
            or_(
                Agent.config['model_id'].astext == str(model_id),
                Agent.plaza_config['model_id'].astext == str(model_id),
            ),
        )

    async def references_model(self, session: AsyncSession, model_id: UUID) -> bool:
        return bool(await self.model_references(session, model_id))

    async def knowledge_references(self, session: AsyncSession, base_id: UUID) -> list[ResourceReference]:
        """返回草稿及发布配置对知识库的实际引用。"""
        return await self._configuration_references(
            session,
            Agent.config['knowledge_base_ids'].contains([str(base_id)]),
        )

    async def skill_references(
        self,
        session: AsyncSession,
        slug: str,
        *,
        owner_id: UUID,
        skill_ids: tuple[UUID, ...] = (),
    ) -> list[ResourceReference]:
        references = await self._configuration_references(
            session,
            (Agent.owner_id == owner_id) & Agent.config['skill_slugs'].contains([slug]),
        )
        runs = await session.scalars(
            select(Run)
            .join(AgentRunAudit, AgentRunAudit.run_id == Run.id)
            .where(
                Run.status.in_(('pending', *ACTIVE_STATUSES)),
                AgentRunAudit.owner_id == owner_id,
                AgentRunAudit.config['skill_slugs'].contains([slug]),
            )
            .order_by(Run.id)
        )
        references.extend(
            ResourceReference('run', row.id, '智能体运行', row.status, row.owner_id, f'/agents/{row.resource_id}')
            for row in runs
        )
        return references

    async def references_skill(self, session: AsyncSession, owner_id: UUID, slug: str) -> bool:
        return bool(await self.skill_references(session, slug, owner_id=owner_id))

    async def audit(
        self,
        session: AsyncSession,
        *,
        run_id: UUID,
        agent_id: UUID,
        owner_id: UUID,
        config: AgentConfig,
        skills: SkillSelection,
        knowledge: list[dict[str, object]],
        execution: dict[str, object] | None = None,
    ) -> None:
        session.add(
            AgentRunAudit(
                run_id=run_id,
                agent_id=agent_id,
                owner_id=owner_id,
                config=config.model_dump(mode='json'),
                skills=skill_values(skills),
                knowledge=knowledge,
                execution=execution,
            )
        )

    @staticmethod
    def create_hash(config: AgentConfig) -> str:
        return hashlib.sha256(json.dumps(config.model_dump(mode='json'), sort_keys=True).encode()).hexdigest()

    async def existing_create(
        self, session: AsyncSession, principal: Principal, request_id: UUID, config: AgentConfig
    ) -> AgentInfo | None:
        key = f'agent-create:{principal.id}:{request_id}'
        await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'), {'key': key})
        row = await session.scalar(
            select(Agent).where(Agent.owner_id == principal.id, Agent.create_client_request_id == request_id)
        )
        if row is None:
            return None
        hashes = {self.create_hash(config)}
        if config.knowledge_top_k == AgentConfig.model_fields['knowledge_top_k'].default:
            # Pre-limit creation requests implicitly used eight hits; their original retries remain equivalent.
            legacy = config.model_dump(mode='json', exclude={'knowledge_top_k'})
            hashes.add(hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest())
        if row.create_fingerprint not in hashes:
            raise AppError('idempotency_conflict', '原创建标识不能用于不同配置', 409)
        return await self.get(session, principal, row.id)

    async def attach_summary(self, session: AsyncSession, parent: ClaimedRun, run_id: UUID) -> None:
        session.add(
            AssistantSummary(
                parent_run_id=parent.id,
                parent_attempt_id=parent.attempt_id,
                run_id=run_id,
                conversation_id=parent.resource_id,
                owner_id=parent.owner_id,
            )
        )
        await session.flush()

    async def summary_for_parent(self, session: AsyncSession, run_id: UUID) -> AssistantSummary | None:
        return await session.get(AssistantSummary, run_id)

    async def summary_for_run(self, session: AsyncSession, run_id: UUID) -> AssistantSummary | None:
        result: AssistantSummary | None = await session.scalar(
            select(AssistantSummary).where(AssistantSummary.run_id == run_id)
        )
        return result

    async def orphan_summaries(self, session: AsyncSession) -> list[tuple[UUID, UUID]]:
        parent, child = aliased(Run), aliased(Run)
        return list(
            (
                await session.execute(
                    select(AssistantSummary.run_id, AssistantSummary.owner_id)
                    .join(parent, parent.id == AssistantSummary.parent_run_id)
                    .join(child, child.id == AssistantSummary.run_id)
                    .where(parent.status.in_(TERMINAL_STATUSES), child.status.not_in(TERMINAL_STATUSES))
                    .limit(100)
                )
            ).tuples()
        )

    async def conversation_agent(self, session: AsyncSession, conversation_id: UUID, owner_id: UUID) -> UUID | None:
        result: UUID | None = await session.scalar(
            select(AssistantConversation.agent_id).where(
                AssistantConversation.id == conversation_id, AssistantConversation.owner_id == owner_id
            )
        )
        return result

    async def summary_run_ids(
        self, session: AsyncSession, parent_id: UUID, owner_id: UUID, *, limit: int
    ) -> list[UUID]:
        return list(
            await session.scalars(
                select(AssistantSummary.run_id)
                .where(AssistantSummary.parent_run_id == parent_id, AssistantSummary.owner_id == owner_id)
                .limit(limit)
            )
        )

    async def summary_parent_id(self, session: AsyncSession, run_id: UUID, owner_id: UUID) -> UUID | None:
        result: UUID | None = await session.scalar(
            select(AssistantSummary.parent_run_id).where(
                AssistantSummary.run_id == run_id, AssistantSummary.owner_id == owner_id
            )
        )
        return result
