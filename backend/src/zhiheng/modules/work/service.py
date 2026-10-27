from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.knowledge.public import Hit
from zhiheng.modules.work.contracts import (
    CreateTask,
    MemberInfo,
    MessageInfo,
    TaskDetail,
    TaskPage,
    TaskStatus,
    TaskSummary,
    UpdateTask,
    safe_text,
)
from zhiheng.modules.work.orm import WorkMember, WorkMessage, WorkTask
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

MAX_MEMBERS = 10


@dataclass(frozen=True)
class MemberSnapshot:
    agent_id: UUID
    agent_owner_id: UUID
    name: str
    published_version: int
    config: dict[str, object]
    skills: list[dict[str, object]]


class WorkService:
    async def reference_run_ids(self, session: AsyncSession, principal: Principal, task_id: UUID) -> tuple[UUID, ...]:
        await self.require(session, principal, task_id)
        return tuple(
            await session.scalars(
                select(WorkMessage.run_id)
                .where(WorkMessage.task_id == task_id, WorkMessage.run_id.is_not(None))
                .distinct()
            )
        )

    async def require(
        self, session: AsyncSession, principal: Principal, task_id: UUID, *, lock: bool = False
    ) -> TaskSummary:
        row = await self._row(session, principal, task_id, lock=lock)
        return self.summary(row)

    async def _row(self, session: AsyncSession, principal: Principal, task_id: UUID, *, lock: bool = False) -> WorkTask:
        query = select(WorkTask).where(WorkTask.id == task_id, WorkTask.deleted_at.is_(None))
        if not principal.is_admin:
            query = query.where(WorkTask.owner_id == principal.id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await session.scalar(query)
        if row is None:
            raise AppError('work_not_found', '任务不存在', 404)
        return row

    @staticmethod
    def summary(row: WorkTask) -> TaskSummary:
        return TaskSummary.model_validate(
            {key: getattr(row, key) for key in TaskSummary.model_fields if key != 'preparation_id'}
        )

    async def detail(self, session: AsyncSession, principal: Principal, task_id: UUID) -> TaskDetail:
        summary = await self.require(session, principal, task_id)
        members = await session.scalars(
            select(WorkMember).where(WorkMember.task_id == task_id).order_by(WorkMember.created_at, WorkMember.id)
        )
        messages = await session.scalars(
            select(WorkMessage).where(WorkMessage.task_id == task_id).order_by(WorkMessage.sequence)
        )
        return TaskDetail(
            **summary.model_dump(),
            members=[
                MemberInfo.model_validate(
                    {
                        key: getattr(row, key)
                        for key in ('agent_id', 'agent_name_snapshot', 'published_version', 'removed_at')
                    }
                )
                for row in members
            ],
            messages=[
                MessageInfo.model_validate({key: getattr(row, key) for key in MessageInfo.model_fields})
                for row in messages
            ],
        )

    async def list_tasks(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        page: int,
        page_size: int,
        search: str,
        status: TaskStatus | None,
    ) -> TaskPage:
        try:
            safe_text(search)
        except ValueError:
            raise AppError('invalid_work_search', '搜索条件无效', 422) from None
        query = select(WorkTask).where(WorkTask.deleted_at.is_(None))
        if not principal.is_admin:
            query = query.where(WorkTask.owner_id == principal.id)
        if search:
            pattern = '%' + search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            query = query.where(or_(WorkTask.title.ilike(pattern), WorkTask.goal.ilike(pattern)))
        if status:
            query = query.where(WorkTask.status == status)
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(
            query.order_by(WorkTask.updated_at.desc(), WorkTask.id).offset((page - 1) * page_size).limit(page_size)
        )
        return TaskPage(items=[self.summary(row) for row in rows], total=total or 0, page=page, page_size=page_size)

    async def existing_create(
        self, session: AsyncSession, owner_id: UUID, request_id: UUID, fingerprint: str
    ) -> UUID | None:
        row = await session.scalar(
            select(WorkTask).where(WorkTask.owner_id == owner_id, WorkTask.create_client_request_id == request_id)
        )
        if row is None:
            return None
        if row.create_fingerprint != fingerprint:
            raise AppError('idempotency_conflict', '请求标识已用于其他内容', 409)
        if row.deleted_at:
            raise AppError('work_not_found', '任务不存在', 404)
        return row.id

    async def create(
        self,
        session: AsyncSession,
        principal: Principal,
        body: CreateTask,
        fingerprint: str,
        members: list[MemberSnapshot],
    ) -> UUID:
        row = WorkTask(
            owner_id=principal.id,
            goal=body.goal,
            title=' '.join(body.goal.split())[:40],
            create_client_request_id=body.client_request_id,
            create_fingerprint=fingerprint,
            create_active_agent_id=body.active_agent_id,
            active_agent_id=body.active_agent_id,
        )
        session.add(row)
        await session.flush()
        for member in members:
            session.add(self.member_row(row.id, member))
        session.add(
            WorkMessage(
                task_id=row.id, client_request_id=body.client_request_id, sequence=1, role='user', content=body.goal
            )
        )
        await session.flush()
        return row.id

    @staticmethod
    def member_row(task_id: UUID, member: MemberSnapshot) -> WorkMember:
        return WorkMember(
            task_id=task_id,
            agent_id=member.agent_id,
            agent_owner_id=member.agent_owner_id,
            agent_name_snapshot=member.name,
            published_version=member.published_version,
            config=member.config,
            skills=member.skills,
        )

    async def editable(
        self,
        session: AsyncSession,
        principal: Principal,
        task_id: UUID,
        version: int,
        *,
        allow_completed: bool = False,
        deleting: bool = False,
    ) -> WorkTask:
        row = await self._row(session, principal, task_id, lock=True)
        if row.version != version:
            raise AppError('work_version_conflict', '任务已更新，请刷新后重试', 409)
        if row.current_run_id:
            raise AppError('work_busy', '任务仍在执行', 409)
        if not deleting and row.status != 'active' and not (allow_completed and row.status == 'completed'):
            raise AppError('work_read_only', '请先重新打开任务', 409)
        return row

    @staticmethod
    def touch(row: WorkTask) -> None:
        row.version += 1
        row.updated_at = utcnow()

    async def reserve_preparation(
        self, session: AsyncSession, principal: Principal, task_id: UUID, expected_version: int
    ) -> int:
        row = await self.editable(session, principal, task_id, expected_version)
        self.touch(row)
        return row.version

    async def release_preparation(
        self, session: AsyncSession, principal: Principal, task_id: UUID, expected_version: int
    ) -> None:
        row = await self._row(session, principal, task_id, lock=True)
        if row.version == expected_version and row.current_run_id is None:
            self.touch(row)

    async def update(self, session: AsyncSession, principal: Principal, task_id: UUID, body: UpdateTask) -> None:
        row = await self.editable(session, principal, task_id, body.expected_version, allow_completed=True)
        fields = body.model_fields_set - {'expected_version'}
        if row.status == 'completed' and (fields != {'status'} or body.status not in ('active', 'archived')):
            raise AppError('work_read_only', '请先重新打开任务', 409)
        if 'active_agent_id' in fields and body.active_agent_id is not None:
            await self.member(session, task_id, body.active_agent_id)
        for key in fields:
            setattr(row, key, getattr(body, key))
        self.touch(row)
        await session.flush()

    async def remove(self, session: AsyncSession, principal: Principal, task_id: UUID, version: int) -> None:
        row = await self.editable(session, principal, task_id, version, deleting=True)
        row.deleted_at = utcnow()
        self.touch(row)

    async def member(self, session: AsyncSession, task_id: UUID, agent_id: UUID) -> MemberSnapshot:
        row = await session.scalar(
            select(WorkMember).where(
                WorkMember.task_id == task_id, WorkMember.agent_id == agent_id, WorkMember.removed_at.is_(None)
            )
        )
        if row is None:
            raise AppError('work_member_not_found', '智能体不在任务中', 404)
        return MemberSnapshot(
            row.agent_id, row.agent_owner_id, row.agent_name_snapshot, row.published_version, row.config, row.skills
        )

    async def change_member(
        self,
        session: AsyncSession,
        principal: Principal,
        task_id: UUID,
        version: int,
        agent_id: UUID,
        snapshot: MemberSnapshot | None,
        *,
        upgrade: bool = False,
    ) -> None:
        row = await self.editable(session, principal, task_id, version)
        members = list(
            await session.scalars(
                select(WorkMember).where(WorkMember.task_id == task_id, WorkMember.removed_at.is_(None))
            )
        )
        current = next((item for item in members if item.agent_id == agent_id), None)
        if current is None and (snapshot is None or upgrade):
            raise AppError('work_member_not_found', '智能体不在任务中', 404)
        if snapshot is not None:
            if current is not None and not upgrade:
                raise AppError('work_member_exists', '成员已存在', 409)
            if current is None and len(members) >= MAX_MEMBERS:
                raise AppError('work_member_limit', '最多 10 个成员', 409)
            if current is None:
                session.add(self.member_row(task_id, snapshot))
            else:
                current.agent_name_snapshot, current.published_version, current.config, current.skills = (
                    snapshot.name,
                    snapshot.published_version,
                    snapshot.config,
                    snapshot.skills,
                )
            if row.active_agent_id is None:
                row.active_agent_id = agent_id
        else:
            assert current is not None
            current.removed_at = utcnow()
            if row.active_agent_id == agent_id:
                row.active_agent_id = next((item.agent_id for item in members if item.agent_id != agent_id), None)
        self.touch(row)
        await session.flush()

    async def require_initial_intent(
        self, session: AsyncSession, principal: Principal, task_id: UUID, request_id: UUID, agent_id: UUID, content: str
    ) -> None:
        """The create key freezes its Agent even when the active selection changes."""
        row = await self._row(session, principal, task_id)
        if request_id != row.create_client_request_id:
            return
        initial = await session.scalar(
            select(WorkMessage).where(WorkMessage.task_id == task_id, WorkMessage.sequence == 1)
        )
        if row.create_active_agent_id != agent_id or initial is None or initial.content != content:
            raise AppError('idempotency_conflict', '首轮请求与创建时的目标或智能体不同', 409)

    async def existing_turn(
        self, session: AsyncSession, task_id: UUID, request_id: UUID, fingerprint: str
    ) -> UUID | None:
        row = await session.scalar(
            select(WorkMessage).where(
                WorkMessage.task_id == task_id,
                WorkMessage.client_request_id == request_id,
                WorkMessage.role == 'assistant',
            )
        )
        if row is None:
            return None
        if row.fingerprint != fingerprint:
            raise AppError('idempotency_conflict', '请求标识已用于其他内容', 409)
        return row.run_id

    async def context(self, session: AsyncSession, task_id: UUID) -> list[MessageInfo]:
        rows = await session.scalars(
            select(WorkMessage).where(WorkMessage.task_id == task_id).order_by(WorkMessage.sequence.desc()).limit(96)
        )
        return list(
            reversed(
                [
                    MessageInfo.model_validate({key: getattr(row, key) for key in MessageInfo.model_fields})
                    for row in rows
                ]
            )
        )

    async def admit(
        self,
        session: AsyncSession,
        principal: Principal,
        task_id: UUID,
        *,
        version: int,
        run_id: UUID,
        request_id: UUID,
        fingerprint: str,
        content: str,
        member: MemberSnapshot,
        knowledge_sources: list[Hit] | None = None,
    ) -> None:
        row = await self.editable(session, principal, task_id, version)
        await self.require_initial_intent(session, principal, task_id, request_id, member.agent_id, content)
        await self.member(session, task_id, member.agent_id)
        last = await session.scalar(select(func.max(WorkMessage.sequence)).where(WorkMessage.task_id == task_id)) or 0
        initial = await session.scalar(
            select(WorkMessage).where(
                WorkMessage.task_id == task_id, WorkMessage.client_request_id == request_id, WorkMessage.role == 'user'
            )
        )
        if initial is not None:
            if last != 1 or initial.content != content:
                raise AppError('idempotency_conflict', '首轮目标已过期或内容不同', 409)
            initial.run_id = run_id
            initial.knowledge_sources = [hit.model_dump(mode='json') for hit in knowledge_sources or []]
        else:
            last += 1
            session.add(
                WorkMessage(
                    task_id=task_id,
                    client_request_id=request_id,
                    sequence=last,
                    role='user',
                    content=content,
                    knowledge_sources=[hit.model_dump(mode='json') for hit in knowledge_sources or []],
                    run_id=run_id,
                )
            )
        session.add(
            WorkMessage(
                task_id=task_id,
                client_request_id=request_id,
                sequence=last + 1,
                role='assistant',
                content='',
                knowledge_sources=[hit.model_dump(mode='json') for hit in knowledge_sources or []],
                status='pending',
                run_id=run_id,
                fingerprint=fingerprint,
                agent_id=member.agent_id,
                agent_name_snapshot=member.name,
                published_version=member.published_version,
                audit={'config': member.config, 'skills': member.skills, 'agent_owner_id': str(member.agent_owner_id)},
            )
        )
        row.current_run_id, row.active_agent_id = run_id, member.agent_id
        self.touch(row)
        await session.flush()

    async def project(
        self,
        session: AsyncSession,
        task_id: UUID,
        run_id: UUID,
        *,
        content: str,
        reasoning: str,
        status: str,
        usage: dict[str, object],
        error: dict[str, object] | None,
        terminal: bool,
    ) -> None:
        task = await session.scalar(
            select(WorkTask).where(WorkTask.id == task_id).with_for_update().execution_options(populate_existing=True)
        )
        if task is None or task.current_run_id != run_id:
            return
        message = await session.scalar(
            select(WorkMessage).where(
                WorkMessage.task_id == task_id, WorkMessage.run_id == run_id, WorkMessage.role == 'assistant'
            )
        )
        assert message is not None
        message.content, message.reasoning, message.status, message.usage, message.error = (
            content,
            reasoning,
            status,
            usage,
            error,
        )
        if terminal:
            task.current_run_id = None
            self.touch(task)
        await session.flush()

    async def references_model(self, session: AsyncSession, model_id: UUID) -> bool:
        return (
            await session.scalar(
                select(WorkMember.id).where(WorkMember.config['model_id'].astext == str(model_id)).limit(1)
            )
        ) is not None

    async def references_skill(self, session: AsyncSession, slug: str) -> bool:
        return (
            await session.scalar(
                select(WorkMember.id).where(WorkMember.config['skill_slugs'].contains([slug])).limit(1)
            )
        ) is not None
