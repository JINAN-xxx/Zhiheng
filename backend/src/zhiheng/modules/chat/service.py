from collections.abc import Sequence
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from zhiheng.modules.chat.contracts import (
    ChatRequestContext,
    ConversationInfo,
    MessageInfo,
    RegenerationTarget,
    SkillSnapshot,
)
from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.modules.files.public import AttachmentSummary, PreparedAttachment
from zhiheng.modules.knowledge.public import Hit
from zhiheng.modules.models.public import ModelImage, ModelMessage
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

MAX_VISIBLE_MESSAGE_ROWS = 200
# Generation 1 is the original reply; at most 99 new versions may follow it.
MAX_REPLY_GENERATIONS = 100


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
        reply = aliased(ChatMessage)
        candidates = await session.execute(
            select(ChatMessage.id, ChatMessage.run_id, func.count(reply.id))
            .outerjoin(
                reply,
                and_(
                    reply.conversation_id == conversation_id,
                    reply.role == 'assistant',
                    or_(
                        reply.reply_to_message_id == ChatMessage.id,
                        and_(reply.reply_to_message_id.is_(None), reply.run_id == ChatMessage.run_id),
                    ),
                ),
            )
            .where(ChatMessage.conversation_id == conversation_id, ChatMessage.role == 'user')
            .group_by(ChatMessage.id)
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(100)
        )
        question_ids: list[UUID] = []
        run_ids: list[UUID] = []
        size = 0
        for question_id, run_id, reply_count in candidates:
            cost = 1 + reply_count
            if size + cost > MAX_VISIBLE_MESSAGE_ROWS:
                if not question_ids:
                    raise AppError('conversation_history_too_large', '当前问题的回复版本超过读取上限', 409)
                break
            question_ids.append(question_id)
            run_ids.append(run_id)
            size += cost
        if not question_ids:
            return []
        rows = list(
            await session.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    or_(
                        ChatMessage.id.in_(question_ids),
                        ChatMessage.reply_to_message_id.in_(question_ids),
                        ChatMessage.run_id.in_(run_ids),
                    ),
                )
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            )
        )
        questions_by_run = {row.run_id: row.id for row in rows if row.role == 'user'}
        return [self._message(row, questions_by_run.get(row.run_id)) for row in reversed(rows)]

    @staticmethod
    def _message(row: ChatMessage, question_id: UUID | None = None) -> MessageInfo:
        return MessageInfo(
            attachments=[AttachmentSummary.model_validate(value) for value in row.attachments],
            knowledge_sources=[Hit.model_validate(value) for value in row.knowledge_sources],
            id=row.id,
            role=cast("Literal['user', 'assistant']", row.role),
            content=row.content,
            reasoning=row.reasoning,
            run_id=row.run_id,
            created_at=row.created_at,
            skill_snapshots=[SkillSnapshot.model_validate(value) for value in row.skill_snapshots],
            reply_to_message_id=(row.reply_to_message_id or question_id) if row.role == 'assistant' else None,
            generation_index=row.generation_index or 1,
            is_complete=row.is_complete,
        )

    async def context(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        *,
        exclude_question_id: UUID | None = None,
    ) -> tuple[ModelMessage, ...]:
        rows = await self._context_rows(session, principal, conversation_id, exclude_question_id=exclude_question_id)
        return tuple(self.context_message(row) for row in rows)

    async def _context_rows(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        *,
        exclude_question_id: UUID | None = None,
    ) -> tuple[ChatMessage, ...]:
        question_query = (
            select(ChatMessage.id, ChatMessage.run_id)
            .join(Conversation)
            .where(
                ChatMessage.conversation_id == conversation_id,
                Conversation.owner_id == principal.id,
                ChatMessage.role == 'user',
                ChatMessage.is_complete.is_(True),
            )
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(50)
        )
        if exclude_question_id is not None:
            question_query = question_query.where(ChatMessage.id != exclude_question_id)
        questions = question_query.cte('context_questions')
        reply = aliased(ChatMessage)
        latest_reply = (
            select(reply.id)
            .where(
                reply.conversation_id == conversation_id,
                reply.role == 'assistant',
                reply.is_complete.is_(True),
                or_(reply.reply_to_message_id == questions.c.id, reply.run_id == questions.c.run_id),
            )
            .order_by(reply.generation_index.desc(), reply.created_at.desc(), reply.id.desc())
            .limit(1)
            .correlate(questions)
            .scalar_subquery()
        )
        rows = list(
            await session.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.is_complete.is_(True),
                    or_(
                        ChatMessage.id.in_(select(questions.c.id)),
                        ChatMessage.id.in_(select(latest_reply).select_from(questions)),
                    ),
                )
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            )
        )
        ordered = list(reversed(rows))
        question_ids = {row.run_id: row.id for row in ordered if row.role == 'user'}
        latest: dict[UUID, ChatMessage] = {}
        for row in ordered:
            if row.role == 'assistant':
                question_id = row.reply_to_message_id or question_ids.get(row.run_id)
                if question_id is not None and (
                    question_id not in latest
                    or (row.generation_index or 1) >= (latest[question_id].generation_index or 1)
                ):
                    latest[question_id] = row
        context: list[ChatMessage] = []
        for row in ordered:
            if row.role == 'user' and row.id != exclude_question_id and row.id in latest:
                context.extend((row, latest[row.id]))
        return tuple(context)

    async def add_user_message(
        self,
        session: AsyncSession,
        *,
        conversation_id: UUID,
        run_id: UUID,
        content: str,
        skill_snapshots: Sequence[SkillSnapshot] = (),
        attachments: Sequence[PreparedAttachment] = (),
        knowledge_sources: Sequence[Hit] = (),
        request_context: ChatRequestContext | None = None,
    ) -> None:
        row = ChatMessage(
            conversation_id=conversation_id,
            run_id=run_id,
            role='user',
            content=content,
            attachments=[value.summary.model_dump(mode='json') for value in attachments],
            knowledge_sources=[value.model_dump(mode='json') for value in knowledge_sources],
            attachment_inputs=[value.model_dump(mode='json') for value in attachments],
            skill_snapshots=[value.model_dump(mode='json') for value in skill_snapshots],
            request_context=request_context.model_dump(mode='json') if request_context else {},
        )
        session.add(row)
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
        assistant = await session.scalar(
            select(ChatMessage).where(ChatMessage.run_id == run_id, ChatMessage.role == 'assistant')
        )
        await session.execute(update(ChatMessage).where(ChatMessage.run_id == run_id).values(is_complete=True))
        if assistant is not None:
            assistant.content, assistant.reasoning, assistant.is_complete = content, reasoning, True
            if assistant.reply_to_message_id is not None:
                await session.execute(
                    update(ChatMessage).where(ChatMessage.id == assistant.reply_to_message_id).values(is_complete=True)
                )
            return
        question = await session.scalar(
            select(ChatMessage).where(ChatMessage.run_id == run_id, ChatMessage.role == 'user')
        )
        session.add(
            ChatMessage(
                conversation_id=conversation_id,
                run_id=run_id,
                role='assistant',
                content=content,
                reasoning=reasoning,
                knowledge_sources=question.knowledge_sources if question else [],
                reply_to_message_id=question.id if question else None,
                is_complete=True,
            )
        )

    async def project_incomplete(self, session: AsyncSession, *, run_id: UUID, content: str, reasoning: str) -> None:
        """Only regeneration already has a durable assistant before completion."""
        await session.execute(
            update(ChatMessage)
            .where(ChatMessage.run_id == run_id, ChatMessage.role == 'assistant', ChatMessage.is_complete.is_(False))
            .values(content=content, reasoning=reasoning)
        )

    async def regeneration_target(
        self, session: AsyncSession, principal: Principal, conversation_id: UUID, message_id: UUID
    ) -> RegenerationTarget:
        conversation = await self.require_conversation(session, principal, conversation_id)
        if conversation.archived:
            raise AppError('conversation_archived', '已归档对话不能重新生成', 409)
        target = await session.scalar(
            select(ChatMessage).where(
                ChatMessage.id == message_id,
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.role == 'assistant',
            )
        )
        if target is None:
            raise AppError('message_not_found', '回复不存在', 404)
        latest = await session.scalar(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id, ChatMessage.role == 'user')
            .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
            .limit(1)
        )
        if (
            latest is None
            or (target.reply_to_message_id or (latest.id if target.run_id == latest.run_id else None)) != latest.id
        ):
            raise AppError('regeneration_not_latest', '只能重新生成最新一轮回复', 409)
        versions = await session.scalars(
            select(ChatMessage.generation_index).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.role == 'assistant',
                or_(ChatMessage.reply_to_message_id == latest.id, ChatMessage.run_id == latest.run_id),
            )
        )
        next_generation_index = max(versions, default=1) + 1
        if next_generation_index > MAX_REPLY_GENERATIONS:
            raise AppError('regeneration_limit_reached', '每个问题最多保留100个回复版本（含原回复）', 409)
        return RegenerationTarget(
            message_id=target.id,
            question=self._message(latest),
            request_context=latest.request_context,
            next_generation_index=next_generation_index,
        )

    async def add_regenerated_reply(
        self,
        session: AsyncSession,
        *,
        conversation_id: UUID,
        run_id: UUID,
        target: RegenerationTarget,
        skill_snapshots: Sequence[SkillSnapshot],
        knowledge_sources: Sequence[Hit],
    ) -> None:
        session.add(
            ChatMessage(
                conversation_id=conversation_id,
                run_id=run_id,
                role='assistant',
                content='',
                reply_to_message_id=target.question.id,
                generation_index=target.next_generation_index,
                skill_snapshots=[value.model_dump(mode='json') for value in skill_snapshots],
                knowledge_sources=[value.model_dump(mode='json') for value in knowledge_sources],
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
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        *,
        exclude_question_id: UUID | None = None,
    ) -> tuple[PreparedAttachment, ...]:
        await self.require_conversation(session, principal, conversation_id)
        rows = await self._context_rows(session, principal, conversation_id, exclude_question_id=exclude_question_id)
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
