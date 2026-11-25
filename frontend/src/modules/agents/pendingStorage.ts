const assistantPrefix = 'zhiheng:assistant:'
const pendingSuffix = ':pending'

export const assistantPendingKey = (ownerId: string, agentId: string, plaza: boolean): string =>
  `${assistantPrefix}${ownerId}:${agentId}${plaza ? ':plaza' : ''}${pendingSuffix}`

export interface AssistantSubmission {
  owner: string
  agent: string
  conversationId: string | null
  createId: string
  requestId: string
}

export function decodeAssistantSubmission(raw: string): AssistantSubmission | null {
  try {
    const value: unknown = JSON.parse(raw)
    if (!value || typeof value !== 'object') return null
    const fields = value as Record<string, unknown>
    const { owner, agent, conversationId, createId, requestId } = fields
    if (
      typeof owner !== 'string' ||
      typeof agent !== 'string' ||
      (conversationId !== null && typeof conversationId !== 'string') ||
      typeof createId !== 'string' ||
      typeof requestId !== 'string'
    )
      return null
    return { owner, agent, conversationId, createId, requestId }
  } catch {
    return null
  }
}

export const encodeAssistantSubmission = (submission: AssistantSubmission): string => {
  const { owner, agent, conversationId, createId, requestId } = submission
  return JSON.stringify({ owner, agent, conversationId, createId, requestId })
}

export function clearAssistantPendingExcept(ownerId: string | null): void {
  try {
    const storage = globalThis.sessionStorage
    const keep = ownerId === null ? null : `${assistantPrefix}${ownerId}:`
    for (let index = storage.length - 1; index >= 0; index -= 1) {
      const key = storage.key(index)
      if (!key?.startsWith(assistantPrefix) || !key.endsWith(pendingSuffix)) continue
      if (!keep || !key.startsWith(keep)) {
        storage.removeItem(key)
        continue
      }
      const raw = storage.getItem(key)
      const submission = raw === null ? null : decodeAssistantSubmission(raw)
      if (!submission || submission.owner !== ownerId) {
        storage.removeItem(key)
        continue
      }
      const identifiers = encodeAssistantSubmission(submission)
      if (raw !== identifiers) storage.setItem(key, identifiers)
    }
  } catch {
    // Browser storage can be unavailable. Authentication must still complete.
  }
}
