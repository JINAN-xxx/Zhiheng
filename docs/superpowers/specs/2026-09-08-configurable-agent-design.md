# Configurable Agents

The user approved implementation of a minimal configurable agent in Agent Extension. Knowledge-base implementation belongs to a colleague; this change supplies a typed retrieval integration contract only.

## Scope

- Agent management at `/extensions/agent`, alongside skill management.
- Owner-scoped CRUD with administrator access. Configure name, description, published chat model, system prompt, enabled skills, knowledge-base IDs, temperature and output token limit.
- Bind an existing Developer API application for external access. The UI can create that application and generate/revoke its existing model-scoped keys without persisting plaintext keys.
- Draft edits are isolated from published configuration. Explicit publish replaces the snapshot; unpublish immediately rejects new public invocations. Delete requires unpublish.
- Snapshots freeze configuration fields and selected resource IDs. Skills are resolved from their current enabled installation at invocation time; skill contents are not version-pinned by this first release.
- Trial chat runs the saved draft as the logged-in owner/administrator. Public calls use `/v1/agents/{agent_id}/chat/completions`, an application's Bearer key, and its existing model grants, quotas and usage ledger.
- Callers supply user/assistant messages and a stream flag. They cannot override model, system prompt, skills, knowledge bindings or sampling settings.
- Stateless conversation history is supplied by the caller; private platform sessions are not reused.

## Retrieval Contract

Expose an async knowledge provider on app state. It receives an explicit execution identity (agent, owner and optional application), an allowlist of knowledge-base IDs and the latest user question. It returns bounded text passages with source metadata. Empty bindings require no provider. Nonempty bindings without a provider fail with a clear `AGENT_KNOWLEDGE_UNAVAILABLE` error. The colleague's provider must authorize the owner and perform retrieval only within those bindings. Retrieval text is contextual data, separate from configured instructions.

## Configuration Contract

Management payloads use camelCase: `name`, `description`, `modelId`, `systemPrompt`, `skillSlugs`, `knowledgeBaseIds`, `applicationId`, `temperature`, `maxTokens`. Updates and publication include `expectedVersion`. Responses additionally include `agentId`, `ownerUserId`, `version`, `status`, `publishedVersion`, `publishedAt`, `createdAt`, `updatedAt`. Status is `draft` or `published`; a published agent may have a newer draft.

Management routes under `/agent_extension/agents`: GET/POST collection; GET/PUT/DELETE `/{agent_id}`; POST `/{agent_id}/publish`, `/{agent_id}/unpublish`, `/{agent_id}/preview`. Page query uses `keyword`, `pageNum`, `pageSize`; rows/total match existing APIs. Preview accepts `{messages: [{role: 'user'|'assistant', content}], stream: false}` and returns the standard data wrapper containing a completion. Public JSON/SSE responses follow existing chat completion conventions.

## Verification

Verify owner isolation, immutable published snapshots, stale-write conflicts, unpublish/delete behavior, permission dependencies, disabled skills, missing retrieval provider, request override rejection, application mismatch, quota reuse and streaming. Verify the form/save/trial/publish/key lifecycle with browser fixtures and responsive screenshots. No real model calls or deployment are implied by local implementation.
