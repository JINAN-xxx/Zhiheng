"""Knowledge dependencies and private-reference projection across active resources."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.knowledge.contracts import (
    JOB_TERMINAL,
    PREPARATION_TERMINAL,
)
from zhiheng.modules.knowledge.graph.orm import GraphConfiguration, GraphJob
from zhiheng.modules.knowledge.orm import (
    IndexGeneration,
    KnowledgeBase,
    KnowledgeDefaults,
    KnowledgeJob,
    RetrievalIntent,
)
from zhiheng.shared.errors import AppError
from zhiheng.shared.references import ResourceReference

if TYPE_CHECKING:
    from zhiheng.modules.knowledge.service import KnowledgeService
    from zhiheng.shared.principal import Principal

PreparationSkillOwner = Callable[[AsyncSession, str, UUID, dict[str, object]], Awaitable[UUID | None]]


class KnowledgeReferences:
    def __init__(
        self,
        catalog: KnowledgeService,
        *,
        preparation_skill_owner: PreparationSkillOwner | None = None,
        base_references: Callable[[AsyncSession, Principal, UUID], Awaitable[dict[str, object]]] | None = None,
    ) -> None:
        self.catalog = catalog
        self.preparation_skill_owner, self.base_references = preparation_skill_owner, base_references

    async def for_base(self, session: AsyncSession, principal: Principal, base_id: UUID) -> dict[str, object]:
        await self.catalog.require(session, principal, base_id, manage=True)
        result = await self.base_references(session, principal, base_id) if self.base_references else {}
        references = cast('list[ResourceReference]', result.get('items', []))
        # Keep the service boundary defensive even if the dependency provider changes.
        visible = [item for item in references if item.owner_id is None or item.owner_id == principal.id]
        hidden_count = int(cast('int', result.get('hidden_count', 0))) + len(references) - len(visible)
        return {
            'items': [
                {'kind': item.kind, 'id': str(item.id), 'name': item.name, 'status': item.status, 'path': item.path}
                for item in visible
            ],
            'hidden_count': hidden_count,
            'total': len(visible) + hidden_count,
        }

    async def confirm_deletion(
        self, session: AsyncSession, principal: Principal, base_id: UUID, *, confirmed: bool
    ) -> None:
        base = await self.catalog.require(session, principal, base_id, manage=True, include_deleted=True)
        if base.deleted_at is not None:
            return
        references = await self.for_base(session, principal, base_id)
        if references['total'] and not confirmed:
            raise AppError(
                'knowledge_referenced', '删除将使关联资源无法再使用此知识库，请确认影响', 409, details=references
            )

    async def has_active_work(self, session: AsyncSession) -> bool:
        """Pending build/graph/preparation work can spawn model steps later."""
        for model, terminal in (
            (KnowledgeJob, JOB_TERMINAL),
            (GraphJob, JOB_TERMINAL),
            (RetrievalIntent, PREPARATION_TERMINAL),
        ):
            if await session.scalar(select(model.id).where(model.status.not_in(terminal)).limit(1)) is not None:
                return True
        return False

    async def references_model(self, session: AsyncSession, model_id: UUID) -> bool:
        return bool(await self.model_references(session, model_id))

    async def model_references(self, session: AsyncSession, model_id: UUID) -> list[ResourceReference]:
        bases = await session.scalars(
            select(KnowledgeBase)
            .outerjoin(GraphConfiguration, GraphConfiguration.base_id == KnowledgeBase.id)
            .where(
                KnowledgeBase.deleted_at.is_(None),
                or_(
                    KnowledgeBase.embed_model_id == model_id,
                    KnowledgeBase.rerank_model_id == model_id,
                    GraphConfiguration.llm_model_id == model_id,
                ),
            )
            .order_by(KnowledgeBase.id)
        )
        references = [
            ResourceReference(
                'knowledge', base.id, base.name, 'active', base.owner_id, f'/knowledge/{base.id}/documents'
            )
            for base in bases
        ]
        defaults = await session.get(KnowledgeDefaults, 1, populate_existing=True)
        if defaults is not None:
            for field, label in (
                ('default_embed_model_id', '知识库默认 Embedding 模型'),
                ('default_rerank_model_id', '知识库默认重排模型'),
            ):
                if getattr(defaults, field) == model_id:
                    references.append(
                        ResourceReference(
                            'knowledge_defaults', model_id, label, 'configured', path='/knowledge/settings'
                        )
                    )
        references.extend(await self._binding_references(session, 'model_id', model_id))
        return references

    async def connection_references(self, session: AsyncSession, connection_id: UUID) -> list[ResourceReference]:
        """Parents can still spawn a child using an older connection after model rebinding."""
        return await self._binding_references(session, 'connection_id', connection_id)

    async def _binding_references(
        self,
        session: AsyncSession,
        field: str,
        value: UUID,
    ) -> list[ResourceReference]:
        references: list[ResourceReference] = []
        jobs = await session.execute(
            select(KnowledgeJob, KnowledgeBase)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeJob.base_id)
            .join(IndexGeneration, IndexGeneration.id == KnowledgeJob.index_revision)
            .where(KnowledgeJob.status.not_in(JOB_TERMINAL), IndexGeneration.binding[field].astext == str(value))
            .order_by(KnowledgeJob.id)
        )
        references.extend(
            ResourceReference(
                'knowledge', job.id, base.name, job.status, base.owner_id, f'/knowledge/{base.id}/documents'
            )
            for job, base in jobs
        )
        graph_jobs = await session.execute(
            select(GraphJob, KnowledgeBase)
            .join(KnowledgeBase, KnowledgeBase.id == GraphJob.base_id)
            .where(
                GraphJob.status.not_in(JOB_TERMINAL),
                GraphJob.operation != 'gc',
                or_(
                    GraphJob.frozen['llm_binding'][field].astext == str(value),
                    GraphJob.frozen['embedding_binding'][field].astext == str(value),
                ),
            )
            .order_by(GraphJob.id)
        )
        references.extend(
            ResourceReference('knowledge', job.id, base.name, job.status, base.owner_id, f'/knowledge/{base.id}/graph')
            for job, base in graph_jobs
        )
        preparations = await session.scalars(
            select(RetrievalIntent)
            .where(
                RetrievalIntent.status.not_in(PREPARATION_TERMINAL),
                or_(
                    RetrievalIntent.dependencies['main_request']['binding'][field].astext == str(value),
                    RetrievalIntent.dependencies['rerank_binding'][field].astext == str(value),
                    RetrievalIntent.dependencies['bases'].contains([{'binding': {field: str(value)}}]),
                ),
            )
            .order_by(RetrievalIntent.id)
        )
        references.extend(self._preparation_reference(row) for row in preparations)
        return references

    async def skill_references(
        self,
        session: AsyncSession,
        slug: str,
        *,
        owner_id: UUID,
        skill_ids: tuple[UUID, ...] = (),
    ) -> list[ResourceReference]:
        rows = await session.scalars(
            select(RetrievalIntent)
            .where(
                RetrievalIntent.status.not_in(PREPARATION_TERMINAL),
                or_(
                    RetrievalIntent.dependencies['config']['skill_slugs'].contains([slug]),
                    RetrievalIntent.dependencies['member_config']['skill_slugs'].contains([slug]),
                    RetrievalIntent.dependencies['agent_config']['skill_slugs'].contains([slug]),
                    RetrievalIntent.dependencies['member_skills'].contains([{'slug': slug}]),
                    RetrievalIntent.request['skill_slugs'].contains([slug]),
                    *(
                        RetrievalIntent.dependencies['member_skills'].contains([{'skill_id': str(skill_id)}])
                        for skill_id in skill_ids
                    ),
                ),
            )
            .order_by(RetrievalIntent.id)
        )
        references = []
        for row in rows:
            if row.resource_type in {'work_turn', 'agent_preview', 'agent_invocation'}:
                if self.preparation_skill_owner is None:
                    continue
                skill_owner = await self.preparation_skill_owner(
                    session, row.resource_type, row.resource_id, row.request
                )
            else:
                skill_owner = row.owner_id
            if skill_owner == owner_id:
                references.append(self._preparation_reference(row))
        return references

    async def agent_references(self, session: AsyncSession, agent_id: UUID) -> list[ResourceReference]:
        rows = await session.scalars(
            select(RetrievalIntent)
            .where(
                RetrievalIntent.status.not_in(PREPARATION_TERMINAL),
                or_(
                    RetrievalIntent.resource_type.in_(('agent_preview', 'agent_invocation'))
                    & (RetrievalIntent.resource_id == agent_id),
                    (RetrievalIntent.resource_type == 'work_turn')
                    & (RetrievalIntent.request['body']['agent_id'].astext == str(agent_id)),
                ),
            )
            .order_by(RetrievalIntent.id)
        )
        return [self._preparation_reference(row) for row in rows]

    @staticmethod
    def _preparation_reference(row: RetrievalIntent) -> ResourceReference:
        path = None
        if row.resource_type in {'agent_preview', 'agent_invocation'}:
            path = f'/agents/{row.resource_id}'
        elif row.resource_type == 'work_turn':
            path = f'/work?taskId={row.resource_id}'
        elif row.resource_type == 'knowledge_query':
            path = f'/knowledge/{row.resource_id}/query'
        return ResourceReference('knowledge', row.id, '知识检索准备任务', row.status, row.owner_id, path)
