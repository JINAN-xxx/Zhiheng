"""Private assistant conversations own history; each turn records its accepted publication.

Assistant output remains authoritative in the shared durable Run projection.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, TypeAdapter
from sqlalchemy import select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.agents.contracts import AgentConfig
from zhiheng.modules.agents.orm import Agent, AgentVersion, AssistantConversation, AssistantTurn
from zhiheng.modules.skills.public import SkillSelection
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
    published_version: int
    created_at: datetime
    updated_at: datetime
    preparation_id: UUID | None = None


class AssistantTurnInfo(BaseModel):
    id: UUID
    request_id: UUID
    sequence: int
    content: str
    run_id: UUID
    published_version: int
    agent_name: str
    sources: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime


@dataclass(frozen=True)
class AssistantConversationValue:
    info: AssistantConversationInfo
    config: AgentConfig
    skills: SkillSelection


class AssistantConversations:
    async def publication(
        self,
        session: AsyncSession,
        principal: Principal,
        agent_id: UUID,
        *,
        lock: bool = False,
        version: int | None = None,
    ) -> tuple[int, AgentConfig, SkillSelection]:
        query = select(Agent).where(Agent.id == agent_id, Agent.owner_id == principal.id, Agent.deleted_at.is_(None))
        if lock:
            query = query.with_for_update(read=True).execution_options(populate_existing=True)
        agent = await session.scalar(query)
        if agent is None:
            raise AppError('agent_not_found', '智能助手不存在', 404)
        if agent.published_version is None:
            raise AppError('assistant_not_published', '请先发布助手，再开始正式对话；草稿可在配置页调试', 409)
        publication = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.agent_id == agent_id,
                AgentVersion.version == (version if version is not None else agent.published_version),
            )
        )
        if publication is None:
            raise AppError('assistant_publication_unavailable', '助手发布版本不可用', 409)
        return (
            publication.version,
            AgentConfig.model_validate(publication.config),
            TypeAdapter(SkillSelection).validate_python({'revisions': publication.skills}),
        )

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
            return self._info(existing)
        version, config, skills = await self.publication(session, principal, agent_id, lock=True)
        row = AssistantConversation(
            owner_id=principal.id,
            agent_id=agent_id,
            create_request_id=request_id,
            published_version=version,
            config=config.model_dump(mode='json'),
            skills=TypeAdapter(SkillSelection).dump_python(skills, mode='json'),
        )
        session.add(row)
        await session.flush()
        return self._info(row)

    async def list_conversations(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, *, before: UUID | None = None
    ) -> list[AssistantConversationInfo]:
        query = select(AssistantConversation).where(
            AssistantConversation.owner_id == principal.id,
            AssistantConversation.agent_id == agent_id,
            AssistantConversation.deleted_at.is_(None),
        )
        # Use creation time and ID, so a reply cannot move an item across the cursor.
        if before is not None:
            # A deleted cursor still anchors pagination without revealing its content.
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
        return [self._info(row) for row in rows]

    async def require(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, conversation_id: UUID, *, lock: bool = False
    ) -> AssistantConversationValue:
        query = select(AssistantConversation).where(
            AssistantConversation.id == conversation_id,
            AssistantConversation.owner_id == principal.id,
            AssistantConversation.agent_id == agent_id,
            AssistantConversation.deleted_at.is_(None),
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await session.scalar(query)
        if row is None:
            raise AppError('assistant_conversation_not_found', '助手对话不存在', 404)
        info = self._info(row)
        try:
            version, config, skills = await self.publication(session, principal, agent_id)
        except AppError as error:
            if error.code not in {'agent_not_found', 'assistant_not_published'}:
                raise
            return AssistantConversationValue(
                info, AgentConfig.model_validate(row.config), TypeAdapter(SkillSelection).validate_python(row.skills)
            )
        info.name, info.description, info.welcome_message = config.name, config.description, config.welcome_message
        info.published_version = version
        return AssistantConversationValue(info, config, skills)

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
        published_version: int,
        agent_name: str,
    ) -> None:
        row = await session.get(AssistantConversation, conversation_id)
        assert row is not None and row.owner_id == principal.id
        session.add(
            AssistantTurn(
                conversation_id=conversation_id,
                owner_id=principal.id,
                request_id=request_id,
                sequence=sequence,
                published_version=published_version,
                agent_name=agent_name,
                content=content,
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

    async def for_run(self, session: AsyncSession, principal: Principal, run_id: UUID) -> AssistantConversationValue:
        turn = await session.scalar(
            select(AssistantTurn).where(AssistantTurn.run_id == run_id, AssistantTurn.owner_id == principal.id)
        )
        if turn is None:
            raise AppError('assistant_conversation_not_found', '助手对话不存在', 404)
        row = await self.authorize(session, principal, turn.conversation_id)
        info = self._info(row)
        version, config, skills = await self.publication(
            session, principal, row.agent_id, version=turn.published_version
        )
        info.published_version = version
        info.name, info.description, info.welcome_message = config.name, config.description, config.welcome_message
        return AssistantConversationValue(info, config, skills)

    async def authorize(
        self, session: AsyncSession, principal: Principal, conversation_id: UUID
    ) -> AssistantConversation:
        row = await session.scalar(
            select(AssistantConversation).where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.owner_id == principal.id,
                AssistantConversation.deleted_at.is_(None),
            )
        )
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
        row = await self.authorize(session, principal, conversation_id)
        if row.version != expected_version:
            raise AppError('assistant_conversation_changed', '会话已更新，请刷新历史记录后重试', 409)
        if remove:
            row.deleted_at = utcnow()
        elif title is not None:
            row.title = title
        row.version += 1
        row.updated_at = utcnow()
        await session.flush()

    @staticmethod
    def _info(row: AssistantConversation) -> AssistantConversationInfo:
        config = AgentConfig.model_validate(row.config)
        return AssistantConversationInfo(
            id=row.id,
            agent_id=row.agent_id,
            title=row.title,
            version=row.version,
            name=config.name,
            description=config.description,
            welcome_message=config.welcome_message,
            published_version=row.published_version,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
