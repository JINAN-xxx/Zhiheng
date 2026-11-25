"""Read a submitted turn without recovering its original input."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.modules.agents.public import AssistantConversations
    from zhiheng.modules.knowledge.public import RetrievalStore
    from zhiheng.runtime.runs import RunStore
    from zhiheng.shared.principal import Principal


class AssistantSubmissionReceipt(BaseModel):
    conversation_id: UUID | None
    accepted: bool


class AssistantReceipts:
    def __init__(self, conversations: AssistantConversations, runs: RunStore, retrieval: RetrievalStore) -> None:
        self.conversations, self.runs, self.retrieval = conversations, runs, retrieval

    async def get(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        agent_id: UUID,
        request_id: UUID,
        create_id: UUID,
        conversation_id: UUID | None,
        plaza: bool,
    ) -> AssistantSubmissionReceipt:
        # Admission uses this lock once preprocessing is complete. A retry must still reuse the request ID.
        await self.runs.lock_request(session, principal.id, request_id)
        recovered = await self.conversations.receipt_conversation(
            session, principal, agent_id=agent_id, create_id=create_id, conversation_id=conversation_id, plaza=plaza
        )
        if recovered is None:
            return AssistantSubmissionReceipt(conversation_id=None, accepted=False)
        accepted = await self.conversations.has_turn_request(session, principal, recovered, request_id)
        if not accepted:
            accepted = await self.retrieval.has_console_request(
                session, actor_id=principal.id, resource_type='agent_chat', resource_id=recovered, request_id=request_id
            )
        return AssistantSubmissionReceipt(conversation_id=recovered, accepted=accepted)
