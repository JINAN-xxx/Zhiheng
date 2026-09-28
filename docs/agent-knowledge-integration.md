# Agent Knowledge Integration

Agent Extension reserves a retrieval interface for the knowledge-base implementation. It does not create knowledge stores, ingest documents, build indexes, select an embedding model, or connect to an external retrieval service.

## Registration

The knowledge-base owner supplies an asynchronous provider implementing `AgentKnowledgeProvider` from `plugins.agent_extension.domain.agent_knowledge` and registers the instance during application startup:

```python
app.state.agent_knowledge_provider = provider
```

The agent invocation service reads `request.app.state.agent_knowledge_provider`. Provider startup, connection management and shutdown belong to the knowledge-base implementation. No provider is registered by default.

## Protocol

```python
from collections.abc import Sequence

from plugins.agent_extension.domain.agent_knowledge import (
    AgentKnowledgeProvider,
    KnowledgeIdentity,
    KnowledgePassage,
    resolve_agent_knowledge,
)


class KnowledgeProvider(AgentKnowledgeProvider):
    async def retrieve(
        self,
        *,
        identity: KnowledgeIdentity,
        knowledge_base_ids: tuple[str, ...],
        query: str,
    ) -> Sequence[KnowledgePassage]:
        ...
```

`KnowledgeIdentity` is an immutable dataclass containing `agent_id: str`, `owner_user_id: int`, and `application_id: str | None`. The application ID identifies the agent's bound developer application when present. The owner identity is taken from stored agent configuration, including when an administrator runs a preview; it is not supplied by the external caller.

Preview calls pass `application_id=None`. Public calls pass the authenticated application's ID after checking that it matches the published binding and that the key can access the configured model.

`knowledge_base_ids` is an immutable allowlist taken from the saved draft for preview or the published snapshot for public invocation. `query` is the latest user message. Providers must not reinterpret the query as authorization or as a request to broaden the allowlist.

`KnowledgePassage` is an immutable dataclass with these fields:

| Field | Type | Requirement |
| --- | --- | --- |
| `content` | `str` | Nonblank text used as reference context. |
| `source_id` | `str` | Nonblank stable source/document identifier. |
| `title` | `str` | Display title; defaults to an empty string. |
| `knowledge_base_id` | `str` | Explicit nonblank ID from the requested allowlist. The dataclass defaults to an empty string, but retrieval rejects that default. |

Return a finite sequence of these dataclass instances, typically a list or tuple. Dictionaries, raw strings and generators do not satisfy the runtime result contract. Return an empty list or tuple when an authorized search has no matches.

## Authorization Ownership

The agent service authorizes agent access and selects stored bindings. The provider must independently authorize `identity.owner_user_id` against every requested knowledge base and enforce all document, tenant and knowledge-base access rules before returning text or source metadata. A matching `knowledge_base_id` field alone does not prove that the owner is authorized.

The provider must retrieve only within `knowledge_base_ids`. It must fail the whole request when a bound knowledge base is missing, inaccessible, or cannot be checked, including after access is revoked. It must never fall back to a global search or substitute another tenant's knowledge base. Access denial must not be disguised as an empty successful search.

External application credentials authorize agent invocation, not access to arbitrary knowledge bases. The application ID is additional execution context and never replaces the owner's authorization. Credentials, internal connection details and private diagnostic text must not appear in passages or public errors.

## Validation and Limits

The shared helper has this invocation contract:

```python
passages = await resolve_agent_knowledge(
    provider,
    agent_id=agent_id,
    owner_user_id=owner_user_id,
    application_id=application_id,
    knowledge_base_ids=knowledge_base_ids,
    query=latest_user_message,
)
```

It returns `tuple[KnowledgePassage, ...]`. Empty bindings immediately return `()` and never call the provider. Nonempty bindings require a provider, even when no documents ultimately match.

The provider call has a default 10-second timeout. The optional `timeout_seconds` keyword exists for controlled tests and internal configuration; it is not a caller override. Providers must use nonblocking I/O, honor cancellation, and impose their own downstream timeouts. An asyncio timeout cannot preempt blocking synchronous work or force a provider that suppresses cancellation to stop.

Each successful retrieval may return at most 8 passages. Their combined UTF-8 byte length must not exceed 24,000 bytes, counting every passage's `content`, `source_id`, `title`, and `knowledge_base_id`. The budget covers returned data; the invocation service adds its own bounded message framing. All fields must be valid UTF-8 strings, and each passage must explicitly claim a knowledge-base ID in the allowlist.

Any invalid passage, out-of-scope source, excessive passage count or excessive byte total rejects the entire result. The helper does not truncate passages or drop invalid sources. Provider absence, exceptions, timeouts and validation failures raise `KnowledgeRetrievalError` with a fixed public response:

```json
{
  "code": "AGENT_KNOWLEDGE_UNAVAILABLE",
  "message": "Agent knowledge retrieval is unavailable."
}
```

The exception exposes `code`, `message`, and `public_dict()`. Provider exception messages are not included in its public text. Caller cancellation propagates normally. Agent invocation catches this error before calling the model.

## Context Handling

Returned passages and source metadata are untrusted contextual data. The helper validates and returns them unchanged; it never executes code, follows links, or applies instructions contained in them. The invocation service serializes passages in a separate user-context message with a clear data boundary. Configured agent and skill instructions stay in the system message. Retrieval content must not grant tools, change access policy, or become system/developer instructions.

This separation does not make model output an authorization mechanism. Provider access checks remain mandatory regardless of any instructions, quotations or claimed privileges in retrieved documents.

## Verification

From `zhiheng-fastapi-backend`, run:

```bash
../.venv/bin/python -m pytest -q tests/plugins/agent_extension/test_agent_knowledge.py
```

Contract tests cover missing and unused providers, identity and binding transfer, valid empty results, invalid passage data, unbound sources, count and UTF-8 limits, provider errors, timeout cancellation, caller cancellation and unmodified contextual text. The knowledge-base implementation must add its own authorization and retrieval tests when registering a real provider.
