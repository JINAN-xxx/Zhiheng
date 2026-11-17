"""Knowledge activity and actionable processing state over currently owned libraries."""

from datetime import datetime, timedelta

from sqlalchemy import exists, func, or_, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.knowledge.orm import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeGrant,
    KnowledgeJob,
    RetrievalIntent,
)
from zhiheng.modules.overview.public import WorkbenchAlert, WorkbenchResource
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

ALERT_LIMIT = 6


class WorkbenchKnowledge:
    async def recent(self, session: AsyncSession, actor: Principal) -> list[WorkbenchResource]:
        if not actor.has_any_permission('platform:knowledge:read'):
            return []
        uses = union_all(
            select(KnowledgeJob.base_id.label('base_id'), KnowledgeJob.created_at.label('at')).where(
                KnowledgeJob.actor_id == actor.id
            ),
            select(RetrievalIntent.resource_id.label('base_id'), RetrievalIntent.created_at.label('at')).where(
                RetrievalIntent.actor_id == actor.id, RetrievalIntent.resource_type == 'knowledge_query'
            ),
        ).subquery()
        at = func.max(uses.c.at)
        rows = await session.execute(
            select(KnowledgeBase.id, KnowledgeBase.name, KnowledgeBase.active_job_id, KnowledgeBase.active_revision, at)
            .join(uses, uses.c.base_id == KnowledgeBase.id)
            .where(
                or_(
                    KnowledgeBase.owner_id == actor.id,
                    KnowledgeBase.id.in_(
                        select(KnowledgeGrant.base_id).where(
                            KnowledgeGrant.user_id == actor.id,
                            KnowledgeGrant.effect == 'allow',
                            or_(KnowledgeGrant.expires_at.is_(None), KnowledgeGrant.expires_at > utcnow()),
                        )
                    ),
                ),
                KnowledgeBase.deleted_at.is_(None),
            )
            .group_by(KnowledgeBase.id)
            .order_by(at.desc(), KnowledgeBase.id)
            .limit(12)
        )
        return [
            WorkbenchResource(
                kind='knowledge',
                id=identifier,
                name=name,
                occurred_at=used,
                status='processing' if job else 'ready' if revision else 'empty',
            )
            for identifier, name, job, revision, used in rows
        ]

    async def activities(self, session: AsyncSession, actor: Principal) -> list[WorkbenchResource]:
        if not actor.has_any_permission('platform:knowledge:read'):
            return []
        bases = list(
            await session.scalars(
                select(KnowledgeBase)
                .where(KnowledgeBase.owner_id == actor.id, KnowledgeBase.deleted_at.is_(None))
                .order_by(KnowledgeBase.created_at.desc())
                .limit(6)
            )
        )
        items = [
            WorkbenchResource(
                kind='knowledge',
                id=base.id,
                name=base.name,
                occurred_at=base.created_at,
                status='ready',
                action='created',
            )
            for base in bases
        ]
        rows = await session.execute(
            select(KnowledgeJob, KnowledgeBase.name)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeJob.base_id)
            .where(
                KnowledgeBase.owner_id == actor.id,
                KnowledgeBase.deleted_at.is_(None),
                KnowledgeJob.finished_at.is_not(None),
                KnowledgeJob.operation.in_(('import', 'rechunk', 'rebuild')),
            )
            .order_by(KnowledgeJob.finished_at.desc())
            .limit(6)
        )
        items.extend(
            WorkbenchResource(
                kind='knowledge',
                id=job.base_id,
                name=name,
                occurred_at=job.finished_at,
                status=job.status,
                action='indexed' if job.status in {'completed', 'partial'} else 'failed',
            )
            for job, name in rows
            if job.finished_at is not None
        )
        return items

    async def alerts(self, session: AsyncSession, actor: Principal, now: datetime) -> list[WorkbenchAlert]:
        if not actor.has_any_permission('platform:knowledge:read'):
            return []
        latest_job = (
            select(KnowledgeJob.id)
            .where(KnowledgeJob.base_id == KnowledgeBase.id)
            .order_by(KnowledgeJob.created_at.desc(), KnowledgeJob.id.desc())
            .correlate(KnowledgeBase)
            .limit(1)
            .scalar_subquery()
        )
        failed_document = (
            exists()
            .where(
                KnowledgeDocument.base_id == KnowledgeBase.id,
                KnowledgeDocument.job_id == KnowledgeJob.id,
                KnowledgeDocument.deleted_at.is_(None),
                KnowledgeDocument.status.in_(('failed', 'interrupted')),
            )
            .correlate(KnowledgeBase, KnowledgeJob)
        )
        rows = await session.execute(
            select(KnowledgeJob, KnowledgeBase.name)
            .join(KnowledgeBase, KnowledgeBase.id == KnowledgeJob.base_id)
            .where(
                KnowledgeBase.owner_id == actor.id,
                KnowledgeBase.deleted_at.is_(None),
                (KnowledgeBase.active_job_id == KnowledgeJob.id)
                | (
                    KnowledgeJob.status.in_(('failed', 'interrupted'))
                    & (KnowledgeJob.created_at >= now - timedelta(days=7))
                    & ((KnowledgeJob.id == latest_job) | failed_document)
                ),
            )
            .order_by(KnowledgeJob.created_at.desc())
            .limit(24)
        )
        unique = []
        seen = set()
        for row in rows:
            if row[0].base_id not in seen:
                unique.append(row)
                seen.add(row[0].base_id)
            if len(unique) == ALERT_LIMIT:
                break
        return [
            WorkbenchAlert(
                id='knowledge-' + str(job.id),
                level='error' if job.status in {'failed', 'interrupted'} else 'info',
                title=name + (' · 资料处理失败' if job.status in {'failed', 'interrupted'} else ' · 资料处理中'),
                description='原文件和已有有效索引保留，可进入知识库查看处理详情。'
                if job.status in {'failed', 'interrupted'}
                else '后台正在处理资料，可离开页面后继续查看。',
                kind='knowledge',
                resource_id=job.base_id,
                occurred_at=job.created_at,
            )
            for job, name in unique
        ]
