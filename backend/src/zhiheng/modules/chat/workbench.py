"""Read skill invocation references without exposing conversation content."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.chat.orm import ChatMessage, Conversation
from zhiheng.shared.principal import Principal


class WorkbenchChat:
    async def skill_uses(self, session: AsyncSession, actor: Principal) -> list[tuple[str, datetime]]:
        if not actor.has_any_permission('platform:skills:read'):
            return []
        rows = await session.execute(
            select(ChatMessage.skill_snapshots, ChatMessage.created_at)
            .join(Conversation, Conversation.id == ChatMessage.conversation_id)
            .where(
                Conversation.owner_id == actor.id,
                Conversation.deleted_at.is_(None),
                ChatMessage.role == 'user',
                ChatMessage.skill_snapshots != [],
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(50)
        )
        return [
            (str(item['slug']), at)
            for skills, at in rows
            for item in skills
            if isinstance(item, dict) and isinstance(item.get('slug'), str)
        ]
