"""Private assistant history and effective SDK memory with one current configuration."""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.contracts import AgentConfig
from zhiheng.modules.agents.execution import AssistantExecutionSpec
from zhiheng.modules.agents.orm import Agent, AgentRunAudit, AssistantConversation, AssistantTurn
from zhiheng.modules.files.public import AttachmentSummary
from zhiheng.modules.skills.public import SkillSelection, resolve_selection
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


class AssistantConversationInfo(BaseModel):
    id: UUID
    agent_id: UUID
    title: str
    version: int
    name: str
    description: str
    welcome_message: str
    created_at: datetime
    updated_at: datetime
    preparation_id: UUID | None = None
    memory_available: bool = True
    summary_used: bool = False


class AssistantTurnInfo(BaseModel):
    id: UUID
    request_id: UUID
    sequence: int
    content: str
    attachments: list[AttachmentSummary] = Field(default_factory=list)
    run_id: UUID
    agent_name: str
    sources: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime


@dataclass(frozen=True)
class AssistantConversationValue:
    info: AssistantConversationInfo
    config: AgentConfig
    skills: SkillSelection


@dataclass(frozen=True)
class AssistantRunValue:
    agent_id: UUID
    owner_id: UUID
    config: AgentConfig
    skills: SkillSelection
    knowledge: list[dict[str, object]]
    execution: AssistantExecutionSpec
    checkpoint_id: str | None


class AssistantConversations:
    async def current(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, *, lock: bool = False
    ) -> tuple[AgentConfig, SkillSelection]:
        query = select(Agent).where(Agent.id == agent_id, Agent.owner_id == principal.id, Agent.deleted_at.is_(None))
        if lock:
            query = query.with_for_update(read=True).execution_options(populate_existing=True)
        agent = await session.scalar(query)
        if agent is None:
            raise AppError('agent_not_found', '智能助手不存在', 404)
        if not agent.enabled:
            raise AppError('assistant_disabled', '请先启用助手，再开始正式对话；配置页仍可调试', 409)
        config = AgentConfig.model_validate(agent.config)
        return config, await resolve_selection(session, principal.id, config.skill_slugs)

    async def create(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, request_id: UUID
    ) -> AssistantConversationInfo:
        key = int.from_bytes(hashlib.sha256(principal.id.bytes + request_id.bytes).digest()[:8], 'big', signed=True)
        await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})
        existing = await session.scalar(
            select(AssistantConversation).where(
                AssistantConversation.owner_id == principal.id, AssistantConversation.create_request_id == request_id
            )
        )
        if existing is not None:
            if existing.deleted_at is not None:
                raise AppError('assistant_conversation_not_found', '助手对话不存在', 404)
            if existing.agent_id != agent_id:
                raise AppError('idempotency_conflict', '此创建标识已用于其他助手', 409)
            return await self.info(session, existing)
        config, skills = await self.current(session, principal, agent_id, lock=True)
        row = AssistantConversation(
            owner_id=principal.id,
            agent_id=agent_id,
            create_request_id=request_id,
            config=config.model_dump(mode='json'),
            skills=TypeAdapter(SkillSelection).dump_python(skills, mode='json'),
        )
        session.add(row)
        await session.flush()
        return await self.info(session, row)

    async def list_conversations(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, *, before: UUID | None = None
    ) -> list[AssistantConversationInfo]:
        query = select(AssistantConversation).where(
            AssistantConversation.owner_id == principal.id,
            AssistantConversation.agent_id == agent_id,
            AssistantConversation.deleted_at.is_(None),
        )
        if before is not None:
            cursor = await session.scalar(
                select(AssistantConversation).where(
                    AssistantConversation.id == before,
                    AssistantConversation.owner_id == principal.id,
                    AssistantConversation.agent_id == agent_id,
                )
            )
            if cursor is None:
                raise AppError('assistant_conversation_not_found', '助手对话不存在', 404)
            query = query.where(
                tuple_(AssistantConversation.created_at, AssistantConversation.id) < (cursor.created_at, before)
            )
        rows = await session.scalars(
            query.order_by(AssistantConversation.created_at.desc(), AssistantConversation.id.desc()).limit(50)
        )
        return [await self.info(session, row) for row in rows]

    async def require(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, conversation_id: UUID, *, lock: bool = False
    ) -> AssistantConversationValue:
        row = await self.authorize(session, principal, conversation_id, lock=lock)
        if row.agent_id != agent_id:
            raise AppError('assistant_conversation_not_found', '助手对话不存在', 404)
        info = await self.info(session, row)
        agent = await session.get(Agent, agent_id)
        config = AgentConfig.model_validate(agent.config if agent and agent.deleted_at is None else row.config)
        # Reads do not require executable skills. Admission resolves and freezes them explicitly.
        return AssistantConversationValue(info, config, SkillSelection(()))

    async def turns(
        self, session: AsyncSession, conversation_id: UUID, *, before: int | None = None, limit: int = 50
    ) -> list[AssistantTurnInfo]:
        query = select(AssistantTurn).where(AssistantTurn.conversation_id == conversation_id)
        if before is not None:
            query = query.where(AssistantTurn.sequence < before)
        rows = await session.scalars(query.order_by(AssistantTurn.sequence.desc()).limit(limit))
        return [AssistantTurnInfo.model_validate(row, from_attributes=True) for row in reversed(list(rows))]

    async def add_turn(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        request_id: UUID,
        content: str,
        run_id: UUID,
        sequence: int,
        sources: list[dict[str, object]],
        agent_name: str,
        attachments: tuple[AttachmentSummary, ...] = (),
    ) -> None:
        row = await self.authorize(session, principal, conversation_id)
        session.add(
            AssistantTurn(
                conversation_id=conversation_id,
                owner_id=principal.id,
                request_id=request_id,
                sequence=sequence,
                agent_name=agent_name,
                content=content,
                attachments=[item.model_dump(mode='json') for item in attachments],
                run_id=run_id,
                sources=sources,
            )
        )
        if sequence == 1 and row.title == '新对话':
            row.title = content.strip()[:80]
        row.version += 1
        row.updated_at = utcnow()
        await session.flush()

    async def conversation_ids(self, session: AsyncSession, agent_id: UUID) -> list[UUID]:
        return list(
            await session.scalars(
                select(AssistantConversation.id).where(
                    AssistantConversation.agent_id == agent_id, AssistantConversation.deleted_at.is_(None)
                )
            )
        )

    async def turn_for_run(self, session: AsyncSession, principal: Principal, run_id: UUID) -> AssistantTurnInfo:
        turn = await session.scalar(
            select(AssistantTurn).where(AssistantTurn.run_id == run_id, AssistantTurn.owner_id == principal.id)
        )
        if turn is None:
            raise AppError('assistant_conversation_not_found', '助手对话不存在', 404)
        return AssistantTurnInfo.model_validate(turn, from_attributes=True)

    async def snapshot(self, session: AsyncSession, run_id: UUID) -> AssistantRunValue:
        row = await session.get(AgentRunAudit, run_id)
        if row is None or row.execution is None:
            raise AppError('assistant_execution_unavailable', '本次助手调用缺少正式执行快照，请重新发送', 409)
        return AssistantRunValue(
            row.agent_id,
            row.owner_id,
            AgentConfig.model_validate(row.config),
            TypeAdapter(SkillSelection).validate_python({'revisions': row.skills}),
            row.knowledge,
            AssistantExecutionSpec.model_validate(row.execution),
            row.checkpoint_id,
        )

    async def authorize(
        self, session: AsyncSession, principal: Principal, conversation_id: UUID, *, lock: bool = False
    ) -> AssistantConversation:
        query = select(AssistantConversation).where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.owner_id == principal.id,
            AssistantConversation.deleted_at.is_(None),
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await session.scalar(query)
        if row is None:
            raise AppError('assistant_conversation_not_found', '助手对话不存在', 404)
        return row

    async def change(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        expected_version: int,
        *,
        title: str | None = None,
        remove: bool = False,
    ) -> None:
        row = await self.authorize(session, principal, conversation_id, lock=True)
        if row.version != expected_version:
            raise AppError('assistant_conversation_changed', '会话已更新，请刷新历史记录后重试', 409)
        if remove:
            row.deleted_at = utcnow()
        elif title is not None:
            row.title = title
        row.version += 1
        row.updated_at = utcnow()
        await session.flush()

    async def finish(
        self, session: AsyncSession, run_id: UUID, result: dict[str, object] | None, *, accepted: bool
    ) -> None:
        audit = await session.get(AgentRunAudit, run_id)
        if audit is None or audit.execution is None or result is None:
            return
        metadata = AssistantExecutionSpec.model_validate(audit.execution).completed(result)
        audit.execution = metadata.model_dump(mode='json')
        audit.checkpoint_id = metadata.completion.checkpoint_id if metadata.completion else None
        turn = await session.scalar(select(AssistantTurn).where(AssistantTurn.run_id == run_id))
        if turn is None:
            return
        row = await self.authorize(session, Principal(audit.owner_id, '', False), turn.conversation_id, lock=True)
        latest = await self.turns(session, row.id, limit=1)
        if (
            accepted
            and audit.checkpoint_id
            and latest
            and latest[0].run_id == run_id
            and turn.sequence == metadata.sequence
            and row.checkpoint_id == metadata.expected_head
            and row.memory_run_id == metadata.base_run_id
        ):
            row.checkpoint_id, row.memory_run_id = audit.checkpoint_id, run_id

    async def info(self, session: AsyncSession, row: AssistantConversation) -> AssistantConversationInfo:
        agent = await session.get(Agent, row.agent_id)
        config = AgentConfig.model_validate(agent.config if agent and agent.deleted_at is None else row.config)
        summary_used = False
        if row.memory_run_id:
            frozen = await self.snapshot(session, row.memory_run_id)
            summary_used = frozen.execution.summary_used
        return AssistantConversationInfo(
            id=row.id,
            agent_id=row.agent_id,
            title=row.title,
            version=row.version,
            name=config.name,
            description=config.description,
            welcome_message=config.welcome_message,
            created_at=row.created_at,
            updated_at=row.updated_at,
            memory_available=row.runtime_version == 1,
            summary_used=summary_used,
        )

    async def deleted_checkpoints(self, session: AsyncSession) -> list[tuple[UUID, UUID]]:
        return list(
            (
                await session.execute(
                    select(AssistantConversation.id, AssistantConversation.owner_id)
                    .where(
                        AssistantConversation.deleted_at.is_not(None),
                        or_(
                            AssistantConversation.checkpoint_cleanup_after.is_(None),
                            AssistantConversation.checkpoint_cleanup_after <= utcnow(),
                        ),
                    )
                    .order_by(
                        AssistantConversation.checkpoint_cleanup_after.asc().nullsfirst(), AssistantConversation.id
                    )
                    .limit(25)
                )
            ).tuples()
        )

    async def cleanup_state(
        self, session: AsyncSession, conversation_id: UUID, owner_id: UUID, *, cleaned: bool = False
    ) -> bool:
        row = await session.scalar(
            select(AssistantConversation)
            .where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.owner_id == owner_id,
                AssistantConversation.deleted_at.is_not(None),
            )
            .with_for_update()
        )
        if row is None or (not cleaned and row.checkpoint_cleanup_after and row.checkpoint_cleanup_after > utcnow()):
            return False
        row.checkpoint_cleanup_after = utcnow() + (timedelta(hours=1) if cleaned else timedelta(minutes=1))
        if cleaned:
            row.checkpoint_id, row.memory_run_id = None, None
        return True
