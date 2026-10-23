from collections.abc import Sequence
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.chat.contracts import ConversationInfo, MessageInfo, SkillSnapshot
from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.modules.files.public import AttachmentSummary, PreparedAttachment
from zhiheng.modules.models.public import ModelImage, ModelMessage
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


class ChatService:
    async def create(self, session: AsyncSession, principal: Principal, title: str = '新对话') -> ConversationInfo:
        row = Conversation(owner_id=principal.id, title=title)
        session.add(row)
        await session.flush()
        return self._conversation(row)

    async def list_conversations(
        self, session: AsyncSession, principal: Principal, *, archived: bool = False
    ) -> list[ConversationInfo]:
        rows = await session.scalars(
            select(Conversation)
            .where(
                Conversation.owner_id == principal.id,
                Conversation.deleted_at.is_(None),
                Conversation.archived.is_(archived),
            )
            .order_by(Conversation.created_at.desc())
            .limit(100)
        )
        return [self._conversation(row) for row in rows]

    async def require_conversation(
        self, session: AsyncSession, principal: Principal, conversation_id: UUID, *, lock: bool = False
    ) -> ConversationInfo:
        query = select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner_id == principal.id, Conversation.deleted_at.is_(None)
        )
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
                attachments=[AttachmentSummary.model_validate(value) for value in row.attachments],
                id=row.id,
                role=cast("Literal['user', 'assistant']", row.role),
                content=row.content,
                reasoning=row.reasoning,
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
        return tuple(self.context_message(row) for row in reversed(rows))

    async def add_user_message(
        self,
        session: AsyncSession,
        *,
        conversation_id: UUID,
        run_id: UUID,
        content: str,
        skill_snapshots: Sequence[SkillSnapshot] = (),
        attachments: Sequence[PreparedAttachment] = (),
    ) -> None:
        session.add(
            ChatMessage(
                conversation_id=conversation_id,
                run_id=run_id,
                role='user',
                content=content,
                attachments=[value.summary.model_dump(mode='json') for value in attachments],
                attachment_inputs=[value.model_dump(mode='json') for value in attachments],
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

    async def complete(
        self, session: AsyncSession, *, conversation_id: UUID, run_id: UUID, content: str, reasoning: str = ''
    ) -> None:
        await session.execute(update(ChatMessage).where(ChatMessage.run_id == run_id).values(is_complete=True))
        session.add(
            ChatMessage(
                conversation_id=conversation_id,
                run_id=run_id,
                role='assistant',
                content=content,
                reasoning=reasoning,
                is_complete=True,
            )
        )

    @staticmethod
    def _conversation(row: Conversation) -> ConversationInfo:
        return ConversationInfo(
            id=row.id, title=row.title, created_at=row.created_at, version=row.version, archived=row.archived
        )

    @staticmethod
    def context_message(row: ChatMessage) -> ModelMessage:
        inputs = [PreparedAttachment.model_validate(value) for value in row.attachment_inputs]
        extra = ''.join(
            f'\n\n[Attachment: {item.summary.original_filename}]\n{item.text}'
            for item in inputs
            if item.image_data is None
        )
        images = tuple(ModelImage(item.image_data) for item in inputs if item.image_data is not None)
        return ModelMessage(cast("Literal['user', 'assistant']", row.role), row.content + extra, images)

    async def attachment_history(
        self, session: AsyncSession, principal: Principal, conversation_id: UUID
    ) -> tuple[PreparedAttachment, ...]:
        await self.require_conversation(session, principal, conversation_id)
        rows = await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id, ChatMessage.is_complete.is_(True))
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(100)
        )
        return tuple(PreparedAttachment.model_validate(value) for row in rows for value in row.attachment_inputs)

    async def edit_conversation(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        *,
        expected_version: int,
        title: str | None = None,
        archived: bool | None = None,
        remove: bool = False,
    ) -> ConversationInfo:
        await self.require_conversation(session, principal, conversation_id, lock=True)
        row = await session.get(Conversation, conversation_id)
        assert row is not None
        if row.version != expected_version:
            raise AppError('conversation_version_conflict', '对话已变化，请刷新', 409)
        if title is not None:
            row.title = title
        if archived is not None:
            row.archived = archived
        if remove:
            row.deleted_at = utcnow()
        row.version += 1
        await session.flush()
        return self._conversation(row)

    async def reference_run_ids(
        self, session: AsyncSession, principal: Principal, conversation_id: UUID
    ) -> tuple[UUID, ...]:
        await self.require_conversation(session, principal, conversation_id)
        return tuple(
            await session.scalars(
                select(ChatMessage.run_id).where(ChatMessage.conversation_id == conversation_id).distinct()
            )
        )
