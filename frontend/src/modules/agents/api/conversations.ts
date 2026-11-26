import { parseManagedFile } from '../../files/public.ts'
import { decodeVoid, http } from '../../../shared/http.ts'
import { list, number, object, string, boolean } from '../../../shared/validation.ts'
import { parseRunSnapshot } from '../../../shared/stream.ts'
import type { RunSnapshot } from '../../../shared/stream.ts'
import { parseKnowledgeHit, parseRunAdmission } from '../../knowledge/public.ts'
import type { KnowledgeHit } from '../../knowledge/public.ts'

export interface AssistantConversation {
  id: string
  agentId: string
  title: string
  version: number
  updatedAt: string
  name: string
  description: string
  welcomeMessage: string
  createdAt: string
  preparationId: string | null
  memoryAvailable: boolean
  summaryUsed: boolean
}
export interface AssistantAttachment {
  parse_warning?: string
  file_id: string
  original_filename: string
  mime: string
  size: number
}
export function parseAssistantAttachments(value: unknown): AssistantAttachment[] {
  if (value == null) return []
  return list((item): AssistantAttachment => {
    const row = object(item)
    return {
      parse_warning: row.parse_warning === undefined ? '' : string(row.parse_warning),
      file_id: string(row.file_id),
      original_filename: string(row.original_filename),
      mime: string(row.mime),
      size: number(row.size)
    }
  })(value)
}
export interface AssistantTurn {
  id: string
  requestId: string | null
  sequence: number
  agentName: string
  content: string
  attachments: AssistantAttachment[]
  run: RunSnapshot
  sources: KnowledgeHit[]
}
function parseConversation(value: unknown): AssistantConversation {
  const d = object(value)
  return {
    id: string(d.id),
    agentId: string(d.agent_id),
    title: string(d.title),
    version: number(d.version),
    updatedAt: string(d.updated_at),
    name: string(d.name),
    description: string(d.description),
    welcomeMessage: string(d.welcome_message),
    memoryAvailable: boolean(d.memory_available),
    summaryUsed: boolean(d.summary_used),
    createdAt: string(d.created_at),
    preparationId: d.preparation_id == null ? null : string(d.preparation_id)
  }
}
function parseDetail(value: unknown) {
  const d = object(value)
  const preparing = d.preparing_message == null ? null : object(d.preparing_message)
  return {
    conversation: parseConversation(d.conversation),
    preparingMessage:
      preparing === null
        ? null
        : {
            requestId: string(preparing.request_id),
            content: string(preparing.content),
            attachments: parseAssistantAttachments(preparing.attachments)
          },
    turns: list((v): AssistantTurn => {
      const row = object(v)
      return {
        id: string(row.id),
        requestId: row.request_id == null ? null : string(row.request_id),
        sequence: number(row.sequence),
        agentName: string(row.agent_name),
        content: string(row.content),
        attachments: parseAssistantAttachments(row.attachments),
        run: parseRunSnapshot(row.run),
        sources: list(parseKnowledgeHit)(row.sources)
      }
    })(d.turns),
    nextBefore: d.next_before == null ? null : number(d.next_before)
  }
}
const base = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/conversations`
export const getAssistantConversations = (
  agentId: string,
  signal: AbortSignal,
  before?: string,
  plaza = false
) => {
  const query = new URLSearchParams()
  if (before) query.set('before', before)
  if (plaza) query.set('plaza', 'true')
  return http.request(base(agentId) + (query.size ? `?${query}` : ''), {
    signal,
    decode: list(parseConversation)
  })
}
export const createAssistantConversation = (
  agentId: string,
  requestId: string,
  signal: AbortSignal,
  plaza = false
) =>
  http.request(base(agentId) + (plaza ? '?plaza=true' : ''), {
    method: 'POST',
    body: { client_request_id: requestId },
    signal,
    decode: parseConversation
  })
export const getAssistantConversation = (
  agentId: string,
  id: string,
  signal: AbortSignal,
  before?: number
) =>
  http.request(`${base(agentId)}/${encodeURIComponent(id)}${before ? `?before=${before}` : ''}`, {
    signal,
    decode: parseDetail
  })
export const getAssistantSubmissionReceipt = (
  agentId: string,
  requestId: string,
  createId: string,
  conversationId: string | null,
  signal: AbortSignal,
  plaza: boolean
) => {
  const query = new URLSearchParams({ create_id: createId })
  if (conversationId) query.set('conversation_id', conversationId)
  if (plaza) query.set('plaza', 'true')
  return http.request(`${base(agentId)}/submissions/${encodeURIComponent(requestId)}?${query}`, {
    signal,
    decode: (value) => {
      const row = object(value)
      return {
        conversationId: row.conversation_id === null ? null : string(row.conversation_id),
        accepted: boolean(row.accepted)
      }
    }
  })
}
export const sendAssistantMessage = (
  agentId: string,
  id: string,
  message: string,
  requestId: string,
  signal: AbortSignal,
  fileIds: readonly string[] = []
) =>
  http.request(`${base(agentId)}/${encodeURIComponent(id)}/runs`, {
    method: 'POST',
    body: {
      message,
      client_request_id: requestId,
      ...(fileIds.length ? { file_ids: [...fileIds] } : {})
    },
    signal,
    decode: parseRunAdmission
  })

export const renameAssistantConversation = (
  agentId: string,
  conversation: AssistantConversation,
  title: string,
  signal: AbortSignal
) =>
  http.request(`${base(agentId)}/${encodeURIComponent(conversation.id)}`, {
    method: 'PATCH',
    body: { title, expected_version: conversation.version },
    signal,
    decode: parseConversation
  })
export const deleteAssistantConversation = (
  agentId: string,
  conversation: AssistantConversation,
  signal: AbortSignal
) =>
  http.request(`${base(agentId)}/${encodeURIComponent(conversation.id)}`, {
    method: 'DELETE',
    body: { expected_version: conversation.version },
    signal,
    decode: decodeVoid
  })

export const uploadAssistantAttachment = (
  agentId: string,
  file: File,
  signal: AbortSignal,
  plaza = false
) =>
  http.request(
    `${base(agentId)}/attachments?filename=${encodeURIComponent(file.name)}${plaza ? '&plaza=true' : ''}`,
    {
      method: 'POST',
      body: file,
      signal,
      decode: parseManagedFile
    }
  )
export const downloadAssistantAttachment = async (
  agentId: string,
  conversationId: string,
  fileId: string,
  signal: AbortSignal
) =>
  (
    await http.open(
      `${base(agentId)}/${encodeURIComponent(conversationId)}/attachments/${encodeURIComponent(fileId)}/download`,
      { signal }
    )
  ).blob()
