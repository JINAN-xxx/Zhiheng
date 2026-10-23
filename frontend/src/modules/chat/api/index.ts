import { decodeVoid, http } from '../../../shared/http.ts'
import { parseRunSnapshot } from '../../../shared/stream.ts'
import { boolean, list, number, object, string } from '../../../shared/validation.ts'
export interface Conversation {
  version: number
  archived: boolean
  id: string
  title: string
  created_at: string
}
export interface ChatAttachment {
  file_id: string
  original_filename: string
  mime: string
  size: number
  sha256: string
  artifact_file_id: string | null
  parser_version: string | null
}
export interface ChatMessage {
  attachments: ChatAttachment[]
  id: string
  role: 'user' | 'assistant'
  content: string
  reasoning: string
  run_id: string | null
  created_at: string
  skill_snapshots: { slug: string; name: string; revision_id: string; content_hash: string }[]
}
function parseConversation(value: unknown): Conversation {
  const data = object(value)
  return {
    id: string(data.id),
    title: string(data.title),
    created_at: string(data.created_at),
    version: data.version === undefined ? 1 : number(data.version),
    archived: data.archived === undefined ? false : boolean(data.archived)
  }
}
function parseMessage(value: unknown): ChatMessage {
  const data = object(value)
  if (data.role !== 'user' && data.role !== 'assistant') throw new Error('不支持的消息角色')
  return {
    attachments:
      data.attachments === undefined
        ? []
        : list((value): ChatAttachment => {
            const item = object(value)
            return {
              file_id: string(item.file_id),
              original_filename: string(item.original_filename),
              mime: string(item.mime),
              size: number(item.size),
              sha256: string(item.sha256),
              artifact_file_id:
                item.artifact_file_id === null ? null : string(item.artifact_file_id),
              parser_version: item.parser_version === null ? null : string(item.parser_version)
            }
          })(data.attachments),
    id: string(data.id),
    role: data.role,
    content: string(data.content),
    reasoning: data.reasoning === undefined ? '' : string(data.reasoning),
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
export const getConversations = (signal: AbortSignal, archived = false) =>
  http.request(`/chat/conversations${archived ? '?archived=true' : ''}`, {
    signal,
    decode: list(parseConversation)
  })
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
    file_ids?: readonly string[]
  },
  signal: AbortSignal
) =>
  http.request(`/chat/conversations/${encodeURIComponent(id)}/runs`, {
    method: 'POST',
    body,
    signal,
    decode: parseRunSnapshot
  })
export { getRun, cancelRun, observeRun } from '../../../shared/runs.ts'

export const getConversation = (id: string, signal: AbortSignal) =>
  http.request(`/chat/conversations/${encodeURIComponent(id)}`, {
    signal,
    decode: parseConversation
  })
export const updateConversation = (
  conversation: Conversation,
  body: { title?: string; archived?: boolean },
  signal: AbortSignal
) =>
  http.request(`/chat/conversations/${encodeURIComponent(conversation.id)}`, {
    method: 'PATCH',
    body: { expected_version: conversation.version, ...body },
    signal,
    decode: parseConversation
  })
export const deleteConversation = (conversation: Conversation, signal: AbortSignal) =>
  http.request(`/chat/conversations/${encodeURIComponent(conversation.id)}`, {
    method: 'DELETE',
    body: { expected_version: conversation.version },
    signal,
    decode: decodeVoid
  })
