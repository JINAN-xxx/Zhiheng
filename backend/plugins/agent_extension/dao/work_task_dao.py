from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.agent_extension.entity.do.work_task_do import AgentWorkMessage, AgentWorkTask, AgentWorkTaskMember
from plugins.agent_extension.entity.vo.work_task_vo import WorkTaskPageQueryModel


class WorkTaskDao:
    @staticmethod
    async def get(db: AsyncSession, task_id: str, *, for_update: bool = False) -> AgentWorkTask | None:
        statement = select(AgentWorkTask).where(AgentWorkTask.task_id == task_id)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return (await db.execute(statement)).scalar_one_or_none()

    @staticmethod
    async def by_create_key(db: AsyncSession, owner_user_id: int, key: str) -> AgentWorkTask | None:
        return (
            await db.execute(
                select(AgentWorkTask).where(
                    AgentWorkTask.owner_user_id == owner_user_id, AgentWorkTask.create_client_message_id == key
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def members(db: AsyncSession, task_id: str) -> list[AgentWorkTaskMember]:
        return list(
            (
                await db.execute(
                    select(AgentWorkTaskMember)
                    .where(AgentWorkTaskMember.task_id == task_id)
                    .order_by(AgentWorkTaskMember.added_at, AgentWorkTaskMember.agent_id)
                )
            ).scalars()
        )

    @staticmethod
    async def messages(db: AsyncSession, task_id: str) -> list[AgentWorkMessage]:
        return list(
            (
                await db.execute(
                    select(AgentWorkMessage)
                    .where(AgentWorkMessage.task_id == task_id)
                    .order_by(AgentWorkMessage.sequence)
                )
            ).scalars()
        )

    @staticmethod
    async def page(
        db: AsyncSession, query: WorkTaskPageQueryModel, owner_user_id: int | None
    ) -> tuple[Sequence[AgentWorkTask], int]:
        where = [AgentWorkTask.status == query.status if query.status else AgentWorkTask.status != 'archived']
        if owner_user_id is not None:
            where.append(AgentWorkTask.owner_user_id == owner_user_id)
        if query.keyword:
            where.append(
                or_(
                    AgentWorkTask.title.contains(query.keyword, autoescape=True),
                    AgentWorkTask.goal.contains(query.keyword, autoescape=True),
                )
            )
        total = (await db.execute(select(func.count()).select_from(AgentWorkTask).where(*where))).scalar_one()
        rows = (
            (
                await db.execute(
                    select(AgentWorkTask)
                    .where(*where)
                    .order_by(AgentWorkTask.updated_at.desc(), AgentWorkTask.task_id)
                    .offset((query.page_num - 1) * query.page_size)
                    .limit(query.page_size)
                )
            )
            .scalars()
            .all()
        )
        return rows, total

    @staticmethod
    async def compare_and_swap(
        db: AsyncSession, task_id: str, version: int, values: dict[str, Any], *, idle: bool = True
    ) -> bool:
        statement = update(AgentWorkTask).where(AgentWorkTask.task_id == task_id, AgentWorkTask.version == version)
        if idle:
            statement = statement.where(AgentWorkTask.current_run_id.is_(None))
        result = await db.execute(
            statement.values(**values, version=version + 1).execution_options(synchronize_session=False)
        )
        return result.rowcount == 1
