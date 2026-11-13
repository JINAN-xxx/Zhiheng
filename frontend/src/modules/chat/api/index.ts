import { parseContextInfo } from '../contextInfo.ts'
import { parseManagedFile } from '../../files/public.ts'
import { generationRequest, parseGenerationConfig } from '../generationSettings.ts'
import type { ChatGenerationConfig, ChatGenerationRequest } from '../generationSettings.ts'
import { decodeVoid, http } from '../../../shared/http.ts'
import { parseKnowledgeHit, parseRunAdmission } from '../../knowledge/public.ts'
import type { KnowledgeHit } from '../../knowledge/public.ts'
import { boolean, list, number, object, string } from '../../../shared/validation.ts'
export interface Conversation {
  last_activity_at?: string
  preparation_id: string | null
  version: number
  archived: boolean
  id: string
  title: string
  created_at: string
}
export interface ChatAttachment {
  file_id: string
  owner_id?: string | null
  original_filename: string
  mime: string
  size: number
  sha256: string
  artifact_file_id: string | null
  parser_version: string | null
}
export interface ChatMessage {
  generation_config?: ChatGenerationConfig | null
  reply_to_message_id: string | null
  generation_index: number
  is_complete: boolean
  feedback: 'up' | 'down' | null
  knowledge_sources: KnowledgeHit[]
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
    preparation_id: data.preparation_id == null ? null : string(data.preparation_id),
    id: string(data.id),
    title: string(data.title),
    created_at: string(data.created_at),
    last_activity_at:
      typeof data.last_activity_at === 'string' ? data.last_activity_at : string(data.created_at),
    version: data.version === undefined ? 1 : number(data.version),
    archived: data.archived === undefined ? false : boolean(data.archived)
  }
}
export function parseMessage(value: unknown): ChatMessage {
  const data = object(value)
  if (data.role !== 'user' && data.role !== 'assistant') throw new Error('不支持的消息角色')
  const generation = data.generation_index === undefined ? 1 : number(data.generation_index)
  if (!Number.isInteger(generation) || generation < 1) throw new Error('回复版本格式不正确')
  const feedback = data.feedback === undefined ? null : data.feedback
  if (feedback !== null && feedback !== 'up' && feedback !== 'down')
    throw new Error('消息反馈格式不正确')
  return {
    generation_config:
      data.generation_config == null ? null : parseGenerationConfig(data.generation_config),
    reply_to_message_id: data.reply_to_message_id == null ? null : string(data.reply_to_message_id),
    generation_index: generation,
    is_complete: data.is_complete === undefined ? true : boolean(data.is_complete),
    feedback,
    knowledge_sources:
      data.knowledge_sources === undefined ? [] : list(parseKnowledgeHit)(data.knowledge_sources),
    attachments:
      data.attachments === undefined
        ? []
        : list((value): ChatAttachment => {
            const item = object(value)
            return {
              file_id: string(item.file_id),
              owner_id: item.owner_id == null ? null : string(item.owner_id),
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
export interface HistoryPage<T> {
  items: T[]
  nextCursor: string | null
}
async function historyPage<T>(
  path: string,
  decode: (value: unknown) => T,
  signal: AbortSignal
): Promise<HistoryPage<T>> {
  return http.request(path, {
    signal,
    decode: (value, response) => ({
      items: list(decode)(value),
      nextCursor: response.headers.get('X-Next-Cursor') || null
    })
  })
}
export const getConversationPage = (
  signal: AbortSignal,
  archived = false,
  search = '',
  cursor?: string | null
) => {
  const params = new URLSearchParams({ limit: '30', archived: String(archived), search })
  if (cursor) params.set('cursor', cursor)
  return historyPage(`/chat/conversations?${params}`, parseConversation, signal)
}
export const getMessagePage = (id: string, signal: AbortSignal, cursor?: string | null) => {
  const params = new URLSearchParams({ limit: '30' })
  if (cursor) params.set('cursor', cursor)
  return historyPage(
    `/chat/conversations/${encodeURIComponent(id)}/messages?${params}`,
    parseMessage,
    signal
  )
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
export const uploadChatAttachment = (file: File, signal: AbortSignal) =>
  http.request(`/chat/attachments?filename=${encodeURIComponent(file.name)}`, {
    method: 'POST',
    body: file,
    signal,
    decode: parseManagedFile
  })
export const getMessages = (id: string, signal: AbortSignal) =>
  http.request(`/chat/conversations/${encodeURIComponent(id)}/messages`, {
    signal,
    decode: list(parseMessage)
  })
export const sendMessage = (
  id: string,
  body: ChatGenerationRequest & {
    model_id: string
    message: string
    client_request_id: string
    skill_slugs?: readonly string[]
    file_ids?: readonly string[]
    knowledge_base_ids?: readonly string[]
  },
  signal: AbortSignal
) =>
  http.request(`/chat/conversations/${encodeURIComponent(id)}/runs`, {
    method: 'POST',
    body: (() => {
      const { system_prompt = '', temperature = null, max_output_tokens = null, ...base } = body
      return { ...base, ...generationRequest({ system_prompt, temperature, max_output_tokens }) }
    })(),
    signal,
    decode: parseRunAdmission
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

export const getContextInfo = (id: string, signal: AbortSignal) =>
  http.request(`/chat/conversations/${encodeURIComponent(id)}/context`, {
    signal,
    decode: parseContextInfo
  })
export const retryContext = (id: string, client_request_id: string, signal: AbortSignal) =>
  http.request(`/chat/conversations/${encodeURIComponent(id)}/context/retry`, {
    method: 'POST',
    body: { client_request_id },
    signal,
    decode: parseContextInfo
  })
