from collections.abc import Sequence
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.chat.contracts import ConversationInfo, MessageInfo, SkillSnapshot
from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.modules.models.public import ModelMessage
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class ChatService:
    async def create(self, session: AsyncSession, principal: Principal, title: str = '新对话') -> ConversationInfo:
        row = Conversation(owner_id=principal.id, title=title)
        session.add(row)
        await session.flush()
        return self._conversation(row)

    async def list_conversations(self, session: AsyncSession, principal: Principal) -> list[ConversationInfo]:
        rows = await session.scalars(
            select(Conversation)
            .where(
                Conversation.owner_id == principal.id,
            )
            .order_by(Conversation.created_at.desc())
            .limit(100)
        )
        return [self._conversation(row) for row in rows]

    async def require_conversation(
        self, session: AsyncSession, principal: Principal, conversation_id: UUID, *, lock: bool = False
    ) -> ConversationInfo:
        query = select(Conversation).where(Conversation.id == conversation_id, Conversation.owner_id == principal.id)
        if lock:
            query = query.with_for_update()
        row = await session.scalar(query)
        if row is None:
            raise AppError('conversation_not_found', '对话不存在', 404)
        return self._conversation(row)

    async def messages(self, session: AsyncSession, principal: Principal, conversation_id: UUID) -> list[MessageInfo]:
        await self.require_conversation(session, principal, conversation_id)
        rows = list(
            await session.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                )
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(200)
            )
        )
        return [
            MessageInfo(
                id=row.id,
                role=cast("Literal['user', 'assistant']", row.role),
                content=row.content,
                run_id=row.run_id,
                created_at=row.created_at,
                skill_snapshots=[SkillSnapshot.model_validate(value) for value in row.skill_snapshots],
            )
            for row in reversed(rows)
        ]

    async def context(
        self, session: AsyncSession, principal: Principal, conversation_id: UUID
    ) -> tuple[ModelMessage, ...]:
        rows = list(
            await session.scalars(
                select(ChatMessage)
                .join(Conversation)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    Conversation.owner_id == principal.id,
                    ChatMessage.is_complete.is_(True),
                )
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(100)
            )
        )
        return tuple(
            ModelMessage(cast("Literal['user', 'assistant']", row.role), row.content) for row in reversed(rows)
        )

    async def add_user_message(
        self,
        session: AsyncSession,
        *,
        conversation_id: UUID,
        run_id: UUID,
        content: str,
        skill_snapshots: Sequence[SkillSnapshot] = (),
    ) -> None:
        session.add(
            ChatMessage(
                conversation_id=conversation_id,
                run_id=run_id,
                role='user',
                content=content,
                skill_snapshots=[value.model_dump(mode='json') for value in skill_snapshots],
            )
        )
        await session.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.title == '新对话',
            )
            .values(title=content[:30])
        )

    async def complete(self, session: AsyncSession, *, conversation_id: UUID, run_id: UUID, content: str) -> None:
        await session.execute(update(ChatMessage).where(ChatMessage.run_id == run_id).values(is_complete=True))
        session.add(
            ChatMessage(
                conversation_id=conversation_id,
                run_id=run_id,
                role='assistant',
                content=content,
                is_complete=True,
            )
        )

    @staticmethod
    def _conversation(row: Conversation) -> ConversationInfo:
        return ConversationInfo(id=row.id, title=row.title, created_at=row.created_at)
