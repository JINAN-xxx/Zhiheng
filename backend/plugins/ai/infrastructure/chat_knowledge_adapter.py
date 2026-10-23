"""Validate bounded knowledge references without depending on the knowledge implementation."""

import asyncio
from collections.abc import Sequence
from typing import Protocol

from plugins.ai.service.platform_chat_service import PlatformChatError, ResolvedChatKnowledgePassage

MAX_KNOWLEDGE_PASSAGES = 8
MAX_KNOWLEDGE_CONTEXT_BYTES = 24_000
KNOWLEDGE_TIMEOUT_SECONDS = 10.0


class KnowledgeRuntime(Protocol):
    async def retrieve_context(
        self, *, owner_user_id: int, knowledge_base_ids: list[str], query: str, top_k: int
    ) -> Sequence[object]: ...


class ChatKnowledgeAdapter:
    def __init__(self, runtime: KnowledgeRuntime | None, *, timeout_seconds: float = KNOWLEDGE_TIMEOUT_SECONDS) -> None:
        self._runtime = runtime
        self._timeout_seconds = timeout_seconds

    async def resolve(
        self, user_id: int, knowledge_base_ids: list[str], query: str
    ) -> tuple[ResolvedChatKnowledgePassage, ...]:
        if not knowledge_base_ids:
            return ()
        if self._runtime is None:
            raise PlatformChatError.knowledge_unavailable()
        try:
            hits = await asyncio.wait_for(
                self._runtime.retrieve_context(
                    owner_user_id=user_id,
                    knowledge_base_ids=list(knowledge_base_ids),
                    query=query,
                    top_k=MAX_KNOWLEDGE_PASSAGES,
                ),
                timeout=self._timeout_seconds,
            )
            return self._validate_hits(hits, knowledge_base_ids)
        except Exception:
            raise PlatformChatError.knowledge_unavailable() from None

    @staticmethod
    def _validate_hits(hits: object, knowledge_base_ids: list[str]) -> tuple[ResolvedChatKnowledgePassage, ...]:
        if not isinstance(hits, Sequence) or isinstance(hits, (str, bytes)) or len(hits) > MAX_KNOWLEDGE_PASSAGES:
            raise PlatformChatError.knowledge_unavailable()
        passages = []
        context_bytes = 0
        for hit in hits:
            content = getattr(hit, 'content', None)
            source_id = getattr(hit, 'source_id', None)
            title = getattr(hit, 'title', None)
            knowledge_base_id = getattr(hit, 'knowledge_base_id', None)
            fields = (content, source_id, title, knowledge_base_id)
            if not all(isinstance(value, str) for value in fields):
                raise PlatformChatError.knowledge_unavailable()
            if not content.strip() or not source_id.strip() or knowledge_base_id not in knowledge_base_ids:
                raise PlatformChatError.knowledge_unavailable()
            context_bytes += sum(len(value.encode('utf-8')) for value in fields)
            if context_bytes > MAX_KNOWLEDGE_CONTEXT_BYTES or len(passages) >= MAX_KNOWLEDGE_PASSAGES:
                raise PlatformChatError.knowledge_unavailable()
            passages.append(ResolvedChatKnowledgePassage(content, source_id, title, knowledge_base_id))
        return tuple(passages)
