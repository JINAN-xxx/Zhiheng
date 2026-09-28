"""Bounded retrieval contract for agent knowledge providers."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

MAX_KNOWLEDGE_PASSAGES = 8
MAX_KNOWLEDGE_CONTEXT_BYTES = 24_000
KNOWLEDGE_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class KnowledgeIdentity:
    agent_id: str
    owner_user_id: int
    application_id: str | None


@dataclass(frozen=True, slots=True)
class KnowledgePassage:
    content: str
    source_id: str
    title: str = ''
    knowledge_base_id: str = ''


class AgentKnowledgeProvider(Protocol):
    async def retrieve(
        self,
        *,
        identity: KnowledgeIdentity,
        knowledge_base_ids: tuple[str, ...],
        query: str,
    ) -> Sequence[KnowledgePassage]:
        """Authorize the owner and retrieve only from the supplied bindings."""
        ...


class KnowledgeRetrievalError(ValueError):
    """Fixed public error without provider credentials or document details."""

    def __init__(self) -> None:
        self.code = 'AGENT_KNOWLEDGE_UNAVAILABLE'
        self.message = 'Agent knowledge retrieval is unavailable.'
        super().__init__(self.message)

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.message}


async def resolve_agent_knowledge(
    provider: AgentKnowledgeProvider | None,
    *,
    agent_id: str,
    owner_user_id: int,
    application_id: str | None,
    knowledge_base_ids: list[str],
    query: str,
    timeout_seconds: float = KNOWLEDGE_TIMEOUT_SECONDS,
) -> tuple[KnowledgePassage, ...]:
    """Return validated contextual data; never interpret retrieval as instructions."""
    if not knowledge_base_ids:
        return ()
    if provider is None:
        raise KnowledgeRetrievalError

    bindings = tuple(knowledge_base_ids)
    identity = KnowledgeIdentity(agent_id, owner_user_id, application_id)
    try:
        passages = await asyncio.wait_for(
            provider.retrieve(identity=identity, knowledge_base_ids=bindings, query=query),
            timeout=timeout_seconds,
        )
        if not isinstance(passages, Sequence) or isinstance(passages, (str, bytes)):
            raise KnowledgeRetrievalError
        if len(passages) > MAX_KNOWLEDGE_PASSAGES:
            raise KnowledgeRetrievalError

        context_bytes = 0
        validated = []
        for passage in passages:
            if len(validated) >= MAX_KNOWLEDGE_PASSAGES:
                raise KnowledgeRetrievalError
            if not isinstance(passage, KnowledgePassage):
                raise KnowledgeRetrievalError
            fields = (passage.content, passage.source_id, passage.title, passage.knowledge_base_id)
            if not all(isinstance(value, str) for value in fields):
                raise KnowledgeRetrievalError
            if not passage.content.strip() or not passage.source_id.strip():
                raise KnowledgeRetrievalError
            if not passage.knowledge_base_id.strip() or passage.knowledge_base_id not in bindings:
                raise KnowledgeRetrievalError
            context_bytes += sum(len(value.encode('utf-8')) for value in fields)
            if context_bytes > MAX_KNOWLEDGE_CONTEXT_BYTES:
                raise KnowledgeRetrievalError
            validated.append(passage)
        return tuple(validated)
    except Exception:
        raise KnowledgeRetrievalError from None
