import { http } from '../../../shared/http.ts'
import { followRun, parseRunSnapshot } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { list, object, string } from '../../../shared/validation.ts'
export interface Conversation {
  id: string
  title: string
  created_at: string
}
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  run_id: string | null
  created_at: string
  skill_snapshots: { slug: string; name: string; revision_id: string; content_hash: string }[]
}
function parseConversation(value: unknown): Conversation {
  const data = object(value)
  return { id: string(data.id), title: string(data.title), created_at: string(data.created_at) }
}
function parseMessage(value: unknown): ChatMessage {
  const data = object(value)
  if (data.role !== 'user' && data.role !== 'assistant') throw new Error('不支持的消息角色')
  return {
    id: string(data.id),
    role: data.role,
    content: string(data.content),
    run_id: data.run_id === null ? null : string(data.run_id),
    created_at: string(data.created_at),
    skill_snapshots:
      data.skill_snapshots === undefined
        ? []
        : list((value) => {
            const item = object(value)
            return {
              slug: string(item.slug),
              name: string(item.name),
              revision_id: string(item.revision_id),
              content_hash: string(item.content_hash)
            }
          })(data.skill_snapshots)
  }
}
export const getConversations = (signal: AbortSignal) =>
  http.request('/chat/conversations', { signal, decode: list(parseConversation) })
export const createConversation = (signal: AbortSignal) =>
  http.request('/chat/conversations', {
    method: 'POST',
    body: {},
    signal,
    decode: parseConversation
  })
export const getMessages = (id: string, signal: AbortSignal) =>
  http.request(`/chat/conversations/${encodeURIComponent(id)}/messages`, {
    signal,
    decode: list(parseMessage)
  })
export const sendMessage = (
  id: string,
  body: {
    model_id: string
    message: string
    client_request_id: string
    skill_slugs?: readonly string[]
  },
  signal: AbortSignal
) =>
  http.request(`/chat/conversations/${encodeURIComponent(id)}/runs`, {
    method: 'POST',
    body,
    signal,
    decode: parseRunSnapshot
  })
export const getRun = (id: string, signal: AbortSignal) =>
  http.request(`/runs/${encodeURIComponent(id)}`, { signal, decode: parseRunSnapshot })
export const cancelRun = (id: string, signal: AbortSignal) =>
  http.request(`/runs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseRunSnapshot
  })
export const observeRun = (
  initial: RunSnapshot,
  update: (run: RunSnapshot) => void,
  signal: AbortSignal
) =>
  followRun(
    {
      get: (signal) => getRun(initial.id, signal),
      open: (signal) => http.open(`/runs/${encodeURIComponent(initial.id)}/events`, { signal })
    },
    initial,
    update,
    signal
  )
