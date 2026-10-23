from __future__ import annotations

import hashlib
import json
from datetime import datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar
from uuid import uuid4

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from plugins.agent_extension.dao.agent_dao import AgentDao
from plugins.agent_extension.dao.work_task_dao import WorkTaskDao
from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.agent_extension.entity.do.work_task_do import AgentWorkMessage, AgentWorkTask, AgentWorkTaskMember
from plugins.agent_extension.entity.vo.work_task_vo import (
    WorkTaskCreateModel,
    WorkTaskDetailModel,
    WorkTaskMemberAddModel,
    WorkTaskMemberModel,
    WorkTaskMemberUpdateModel,
    WorkTaskMessageModel,
    WorkTaskModel,
    WorkTaskPageQueryModel,
    WorkTaskUpdateModel,
)
from plugins.agent_extension.service.agent_service import AgentService
from plugins.ai.public.model_queries import lock_model_reference

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from plugins.agent_extension.entity.vo.agent_vo import AgentPublishedModel


T = TypeVar('T')
MAX_MEMBERS = 10


def session_operation(method: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    @wraps(method)
    async def wrapped(self: WorkTaskService, *args: object, **kwargs: object) -> T:
        try:
            return await method(self, *args, **kwargs)
        finally:
            if self.db.in_transaction():
                await self.db.rollback()

    return wrapped


def conflict(message: str = '任务版本已变化，请刷新后重试') -> AgentServiceError:
    return AgentServiceError('WORK_CONFLICT', message, status_code=409)


class WorkTaskService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def require_task(
        self, task_id: str, actor_user_id: int, is_admin: bool, *, for_update: bool = False
    ) -> AgentWorkTask:
        task = await WorkTaskDao.get(self.db, task_id, for_update=for_update)
        if task is None or (not is_admin and task.owner_user_id != actor_user_id):
            raise AgentServiceError('WORK_NOT_FOUND', '任务不存在', status_code=404)
        return task

    async def detail(self, task: AgentWorkTask, actor_user_id: int, is_admin: bool) -> WorkTaskDetailModel:
        members = []
        for member in await WorkTaskDao.members(self.db, task.task_id):
            if member.removed_at is not None:
                continue
            agent = await AgentDao.get_by_id(self.db, member.agent_id)
            available = bool(
                agent
                and agent.status == 'published'
                and agent.published_config
                and agent.published_version
                and (is_admin or agent.owner_user_id == actor_user_id)
            )
            item = WorkTaskMemberModel.model_validate(member)
            item.available = available
            item.latest_published_version = agent.published_version if available else None
            members.append(item)
        messages = [
            WorkTaskMessageModel.model_validate(message)
            for message in await WorkTaskDao.messages(self.db, task.task_id)
        ]
        return WorkTaskDetailModel(
            **WorkTaskModel.model_validate(task).model_dump(), members=members, messages=messages
        )

    @session_operation
    async def get_task(self, task_id: str, *, actor_user_id: int, is_admin: bool = False) -> WorkTaskDetailModel:
        task = await self.require_task(task_id, actor_user_id, is_admin)
        return await self.detail(task, actor_user_id, is_admin)

    @session_operation
    async def list_tasks(
        self, query: WorkTaskPageQueryModel, *, actor_user_id: int, is_admin: bool = False
    ) -> dict[str, Any]:
        rows, total = await WorkTaskDao.page(self.db, query, None if is_admin else actor_user_id)
        return {
            'rows': [WorkTaskModel.model_validate(row).model_dump(mode='json', by_alias=True) for row in rows],
            'total': total,
            'pageNum': query.page_num,
            'pageSize': query.page_size,
        }

    @session_operation
    async def create_task(
        self, payload: WorkTaskCreateModel, *, actor_user_id: int, is_admin: bool = False
    ) -> WorkTaskDetailModel:
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    'goal': payload.goal,
                    'agentIds': sorted(set(payload.agent_ids)),
                    'activeAgentId': payload.active_agent_id,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        existing = await WorkTaskDao.by_create_key(self.db, actor_user_id, payload.client_message_id)
        if existing is not None:
            if existing.create_request_hash != request_hash:
                raise conflict('该消息标识已用于其他任务，请刷新后重试')
            return await self.detail(existing, actor_user_id, is_admin)
        agents = [
            await AgentService(self.db).get_usable_published_agent(
                agent_id, actor_user_id=actor_user_id, is_admin=is_admin, protect_reference=False
            )
            for agent_id in dict.fromkeys(payload.agent_ids)
        ]
        # All models are locked in one order even when users choose members in a different order.
        for model_id in sorted({agent.model_id for agent in agents}):
            if not await lock_model_reference(self.db, model_id):
                raise AgentServiceError('AGENT_MODEL_UNAVAILABLE', '智能体模型不可用')
        task = AgentWorkTask(
            task_id=str(uuid4()),
            owner_user_id=actor_user_id,
            create_client_message_id=payload.client_message_id,
            create_request_hash=request_hash,
            title=' '.join(payload.goal.split())[:40],
            goal=payload.goal,
            active_agent_id=payload.active_agent_id,
        )
        try:
            self.db.add(task)
            await self.db.flush()
            for agent in agents:
                self.db.add(self.snapshot(task.task_id, agent))
            self.db.add(
                AgentWorkMessage(
                    message_id=str(uuid4()),
                    task_id=task.task_id,
                    client_message_id=payload.client_message_id,
                    role='user',
                    content=payload.goal,
                )
            )
            await self.db.flush()
            result = await self.detail(task, actor_user_id, is_admin)
            await self.db.commit()
            return result
        except IntegrityError:
            await self.db.rollback()
            existing = await WorkTaskDao.by_create_key(self.db, actor_user_id, payload.client_message_id)
            if existing is None:
                raise
            if existing.create_request_hash != request_hash:
                raise conflict('该消息标识已用于其他任务，请刷新后重试') from None
            return await self.detail(existing, actor_user_id, is_admin)

    @staticmethod
    def snapshot(task_id: str, agent: AgentPublishedModel) -> AgentWorkTaskMember:
        return AgentWorkTaskMember(
            task_id=task_id,
            agent_id=agent.agent_id,
            agent_name_snapshot=agent.name,
            published_version=agent.published_version,
            config_snapshot=agent.model_dump(mode='json', by_alias=True),
        )

    async def claim_edit(
        self,
        task: AgentWorkTask,
        expected_version: int,
        values: dict[str, Any] | None = None,
        *,
        deleting: bool = False,
    ) -> None:
        if task.current_run_id:
            raise conflict('任务正在运行，请停止后再修改')
        if task.status == 'archived' and not deleting:
            raise conflict('归档任务只允许查看或删除')
        if (
            task.status == 'completed'
            and not deleting
            and (not values or set(values) != {'status'} or values['status'] not in ('active', 'archived'))
        ):
            raise conflict('请先重新打开已完成的任务')
        if task.version != expected_version:
            raise conflict()
        if not await WorkTaskDao.compare_and_swap(
            self.db, task.task_id, expected_version, {**(values or {}), 'updated_at': datetime.now()}
        ):
            raise conflict()
        await self.db.refresh(task)

    @session_operation
    async def update_task(
        self, task_id: str, payload: WorkTaskUpdateModel, *, actor_user_id: int, is_admin: bool = False
    ) -> WorkTaskDetailModel:
        task = await self.require_task(task_id, actor_user_id, is_admin, for_update=True)
        values = payload.model_dump(exclude_unset=True, exclude={'expected_version'})
        await self.claim_edit(task, payload.expected_version, values)
        result = await self.detail(task, actor_user_id, is_admin)
        await self.db.commit()
        return result

    @session_operation
    async def delete_task(self, task_id: str, *, actor_user_id: int, is_admin: bool = False) -> None:
        task = await self.require_task(task_id, actor_user_id, is_admin, for_update=True)
        await self.claim_edit(task, task.version, deleting=True)
        for entity in (AgentWorkMessage, AgentWorkTaskMember, AgentWorkTask):
            await self.db.execute(delete(entity).where(entity.task_id == task_id))
        await self.db.commit()

    @session_operation
    async def add_member(
        self, task_id: str, payload: WorkTaskMemberAddModel, *, actor_user_id: int, is_admin: bool = False
    ) -> WorkTaskDetailModel:
        task = await self.require_task(task_id, actor_user_id, is_admin, for_update=True)
        members = await WorkTaskDao.members(self.db, task_id)
        active = [member for member in members if member.removed_at is None]
        if len(active) >= MAX_MEMBERS or any(member.agent_id == payload.agent_id for member in active):
            raise conflict('智能体已加入任务或成员数量已达上限')
        agent = await AgentService(self.db).get_usable_published_agent(
            payload.agent_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        await self.claim_edit(
            task, payload.expected_version, {'active_agent_id': task.active_agent_id or agent.agent_id}
        )
        previous = next((member for member in members if member.agent_id == agent.agent_id), None)
        snapshot = self.snapshot(task_id, agent)
        if previous is None:
            self.db.add(snapshot)
        else:
            previous.config_snapshot = snapshot.config_snapshot
            previous.agent_name_snapshot = snapshot.agent_name_snapshot
            previous.published_version = snapshot.published_version
            previous.removed_at = None
            previous.added_at = datetime.now()
        await self.db.flush()
        result = await self.detail(task, actor_user_id, is_admin)
        await self.db.commit()
        return result

    @session_operation
    async def update_member(
        self,
        task_id: str,
        agent_id: str,
        payload: WorkTaskMemberUpdateModel,
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> WorkTaskDetailModel:
        task = await self.require_task(task_id, actor_user_id, is_admin, for_update=True)
        member = await self.db.get(AgentWorkTaskMember, (task_id, agent_id))
        if member is None or member.removed_at is not None:
            raise AgentServiceError('WORK_MEMBER_NOT_FOUND', '任务成员不存在', status_code=404)
        agent = await AgentService(self.db).get_usable_published_agent(
            agent_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        if agent.published_version != payload.expected_published_version:
            raise conflict('智能体发布版本已变化，请确认最新版本')
        await self.claim_edit(task, payload.expected_version)
        member.agent_name_snapshot = agent.name
        member.published_version = agent.published_version
        member.config_snapshot = agent.model_dump(mode='json', by_alias=True)
        await self.db.flush()
        result = await self.detail(task, actor_user_id, is_admin)
        await self.db.commit()
        return result

    @session_operation
    async def remove_member(
        self, task_id: str, agent_id: str, expected_version: int, *, actor_user_id: int, is_admin: bool = False
    ) -> WorkTaskDetailModel:
        task = await self.require_task(task_id, actor_user_id, is_admin, for_update=True)
        members = await WorkTaskDao.members(self.db, task_id)
        member = next((member for member in members if member.agent_id == agent_id and member.removed_at is None), None)
        if member is None:
            raise AgentServiceError('WORK_MEMBER_NOT_FOUND', '任务成员不存在', status_code=404)
        remaining = [member.agent_id for member in members if member.agent_id != agent_id and member.removed_at is None]
        values = {'active_agent_id': remaining[0] if remaining else None} if task.active_agent_id == agent_id else {}
        await self.claim_edit(task, expected_version, values)
        member.removed_at = datetime.now()
        await self.db.flush()
        result = await self.detail(task, actor_user_id, is_admin)
        await self.db.commit()
        return result
