import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Sequence
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.chat.actions.contracts import (
    CreatedShareInfo,
    FeedbackInfo,
    FeedbackValue,
    PublicSharedMessage,
    ShareInfo,
    SharePreview,
)
from zhiheng.modules.chat.actions.orm import MessageFeedback, MessageShare
from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    from zhiheng.modules.chat.service import ChatService
    from zhiheng.modules.identity.public import IdentityService

SHARE_TOKEN_LENGTH = 43
SHARE_TOKEN_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}')


class MessageActionService:
    """Message actions flush into the caller's transaction; they never commit."""

    def __init__(self, chat: 'ChatService', identity: 'IdentityService') -> None:
        self._chat = chat
        self._identity = identity

    async def set_feedback(
        self,
        session: AsyncSession,
        principal: Principal,
        message_id: UUID,
        *,
        value: FeedbackValue | None,
    ) -> FeedbackInfo:
        await self._require_message(session, principal, message_id)
        row = await session.scalar(
            select(MessageFeedback)
            .where(MessageFeedback.owner_id == principal.id, MessageFeedback.message_id == message_id)
            .with_for_update()
        )
        if value is None:
            if row is not None:
                await session.delete(row)
        elif row is None:
            session.add(MessageFeedback(owner_id=principal.id, message_id=message_id, value=value))
        else:
            row.value = value
            row.updated_at = utcnow()
        await session.flush()
        return FeedbackInfo(message_id=message_id, value=value)

    async def conversation_feedback(
        self, session: AsyncSession, principal: Principal, conversation_id: UUID
    ) -> list[FeedbackInfo]:
        await self._chat.require_conversation(session, principal, conversation_id)
        rows = await session.scalars(
            select(MessageFeedback)
            .join(ChatMessage, ChatMessage.id == MessageFeedback.message_id)
            .where(MessageFeedback.owner_id == principal.id, ChatMessage.conversation_id == conversation_id)
            .order_by(MessageFeedback.message_id)
        )
        return [FeedbackInfo(message_id=row.message_id, value=row.value) for row in rows]

    async def feedback_for_messages(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        conversation_id: UUID,
        message_ids: Sequence[UUID],
    ) -> dict[UUID, FeedbackValue]:
        await self._chat.require_conversation(session, principal, conversation_id)
        if not message_ids:
            return {}
        rows = await session.scalars(
            select(MessageFeedback)
            .join(ChatMessage, ChatMessage.id == MessageFeedback.message_id)
            .where(
                MessageFeedback.owner_id == principal.id,
                ChatMessage.conversation_id == conversation_id,
                MessageFeedback.message_id.in_(message_ids),
            )
        )
        return {row.message_id: row.value for row in rows}

    async def share_preview(
        self,
        session: AsyncSession,
        principal: Principal,
        message_id: UUID,
        *,
        include_question: bool = True,
    ) -> SharePreview:
        row = await self._require_message(session, principal, message_id)
        return await self._preview(session, row, include_question=include_question)

    async def create_share(
        self,
        session: AsyncSession,
        principal: Principal,
        message_id: UUID,
        *,
        include_question: bool,
        expires_in_days: int,
        expected_content_hash: str,
    ) -> CreatedShareInfo:
        message = await self._require_message(session, principal, message_id)
        preview = await self._preview(session, message, include_question=include_question)
        if not hmac.compare_digest(preview.content_hash, expected_content_hash):
            raise AppError('share_content_changed', '分享内容已变化，请重新预览', 409)
        token = secrets.token_urlsafe(32)
        now = utcnow()
        row = MessageShare(
            owner_id=principal.id,
            message_id=message.id,
            conversation_id=message.conversation_id,
            token_hash=self._hash_token(token),
            question=preview.question,
            answer=preview.answer,
            include_question=include_question,
            content_hash=preview.content_hash,
            created_at=now,
            expires_at=now + timedelta(days=expires_in_days),
        )
        session.add(row)
        await session.flush()
        return CreatedShareInfo(
            id=row.id,
            message_id=row.message_id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
            token=token,
        )

    async def list_shares(self, session: AsyncSession, principal: Principal, message_id: UUID) -> list[ShareInfo]:
        await self._require_message(session, principal, message_id)
        rows = await session.scalars(
            select(MessageShare)
            .where(MessageShare.message_id == message_id, MessageShare.owner_id == principal.id)
            .order_by(MessageShare.created_at.desc(), MessageShare.id.desc())
        )
        return [self._share_info(row) for row in rows]

    async def revoke_share(self, session: AsyncSession, principal: Principal, share_id: UUID) -> None:
        locations = await session.execute(
            select(MessageShare.conversation_id, MessageShare.message_id).where(
                MessageShare.id == share_id, MessageShare.owner_id == principal.id
            )
        )
        location = locations.tuples().one_or_none()
        if location is None:
            raise self._share_not_found()
        conversation_id, message_id = location
        # Revocation remains available after soft deletion; ownership still comes from the source conversation.
        conversation = await session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.owner_id == principal.id)
            .with_for_update()
        )
        if conversation is None:
            raise self._share_not_found()
        await session.scalar(select(ChatMessage.id).where(ChatMessage.id == message_id).with_for_update())
        row = await session.scalar(
            select(MessageShare)
            .where(MessageShare.id == share_id, MessageShare.owner_id == principal.id)
            .with_for_update()
        )
        if row is None:
            raise self._share_not_found()
        if row.revoked_at is None:
            row.revoked_at = utcnow()
        await session.flush()

    async def read_shared(self, session: AsyncSession, *, token: str) -> PublicSharedMessage:
        if len(token) != SHARE_TOKEN_LENGTH or SHARE_TOKEN_PATTERN.fullmatch(token) is None:
            raise self._share_not_found()
        # This indexed lookup only locates the source; locks always start at the conversation.
        locations = await session.execute(
            select(MessageShare.conversation_id, MessageShare.message_id).where(
                MessageShare.token_hash == self._hash_token(token)
            )
        )
        location = locations.tuples().one_or_none()
        if location is None:
            raise self._share_not_found()
        conversation_id, source_message_id = location
        conversation = await session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.deleted_at.is_(None))
            .with_for_update(read=True)
        )
        if conversation is None:
            raise self._share_not_found()
        message_id = await session.scalar(
            select(ChatMessage.id)
            .where(ChatMessage.id == source_message_id, ChatMessage.conversation_id == conversation.id)
            .with_for_update(read=True)
        )
        if message_id is None:
            raise self._share_not_found()
        row = await session.scalar(
            select(MessageShare)
            .where(
                MessageShare.token_hash == self._hash_token(token),
                MessageShare.owner_id == conversation.owner_id,
                MessageShare.revoked_at.is_(None),
                MessageShare.expires_at > utcnow(),
            )
            .with_for_update(read=True)
        )
        if row is None or row.expires_at <= utcnow() or not await self._identity.is_active(session, row.owner_id):
            raise self._share_not_found()
        return PublicSharedMessage(
            question=row.question, answer=row.answer, created_at=row.created_at, expires_at=row.expires_at
        )

    async def _require_message(self, session: AsyncSession, principal: Principal, message_id: UUID) -> ChatMessage:
        conversation_id = await session.scalar(
            select(ChatMessage.conversation_id)
            .join(Conversation, Conversation.id == ChatMessage.conversation_id)
            .where(
                ChatMessage.id == message_id,
                Conversation.owner_id == principal.id,
                Conversation.deleted_at.is_(None),
            )
        )
        if conversation_id is None:
            raise AppError('message_not_found', '消息不存在', 404)
        await self._chat.require_conversation(session, principal, conversation_id, lock=True)
        row = await session.scalar(
            select(ChatMessage)
            .where(ChatMessage.id == message_id, ChatMessage.conversation_id == conversation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise AppError('message_not_found', '消息不存在', 404)
        return row

    async def _preview(self, session: AsyncSession, row: ChatMessage, *, include_question: bool) -> SharePreview:
        if row.role != 'assistant' or not row.content.strip():
            raise AppError('message_not_shareable', '只能分享有正文的助手消息', 422)
        question: str | None = None
        if include_question:
            query = select(ChatMessage.content).where(
                ChatMessage.conversation_id == row.conversation_id, ChatMessage.role == 'user'
            )
            if row.reply_to_message_id is not None:
                query = query.where(ChatMessage.id == row.reply_to_message_id)
            else:
                query = query.where(ChatMessage.run_id == row.run_id)
            question = await session.scalar(query)
        snapshot = json.dumps(
            {
                'message_id': str(row.id),
                'include_question': include_question,
                'question': question,
                'answer': row.content,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
        return SharePreview(
            message_id=row.id,
            question=question,
            answer=row.content,
            content_hash=hashlib.sha256(snapshot.encode('utf-8')).hexdigest(),
        )

    @staticmethod
    def _share_info(row: MessageShare) -> ShareInfo:
        return ShareInfo(
            id=row.id,
            message_id=row.message_id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            revoked_at=row.revoked_at,
        )

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode('ascii')).hexdigest()

    @staticmethod
    def _share_not_found() -> AppError:
        return AppError('share_not_found', '分享不存在或已失效', 404)
