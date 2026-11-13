import { decodeVoid, http } from '../../../shared/http.ts'
import { list, number, object, string } from '../../../shared/validation.ts'
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
  publishedVersion: number
  createdAt: string
  preparationId: string | null
}
export interface AssistantTurn {
  id: string
  requestId: string | null
  sequence: number
  agentName: string
  publishedVersion: number
  content: string
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
    publishedVersion: number(d.published_version),
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
        : { requestId: string(preparing.request_id), content: string(preparing.content) },
    turns: list((v): AssistantTurn => {
      const row = object(v)
      return {
        id: string(row.id),
        requestId: row.request_id == null ? null : string(row.request_id),
        sequence: number(row.sequence),
        agentName: string(row.agent_name),
        publishedVersion: number(row.published_version),
        content: string(row.content),
        run: parseRunSnapshot(row.run),
        sources: list(parseKnowledgeHit)(row.sources)
      }
    })(d.turns),
    nextBefore: d.next_before == null ? null : number(d.next_before)
  }
}
const base = (agentId: string) => `/agents/${encodeURIComponent(agentId)}/conversations`
export const getAssistantConversations = (agentId: string, signal: AbortSignal, before?: string) =>
  http.request(base(agentId) + (before ? `?before=${encodeURIComponent(before)}` : ''), {
    signal,
    decode: list(parseConversation)
  })
export const createAssistantConversation = (
  agentId: string,
  requestId: string,
  signal: AbortSignal
) =>
  http.request(base(agentId), {
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
export const sendAssistantMessage = (
  agentId: string,
  id: string,
  message: string,
  requestId: string,
  signal: AbortSignal
) =>
  http.request(`${base(agentId)}/${encodeURIComponent(id)}/runs`, {
    method: 'POST',
    body: { message, client_request_id: requestId },
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
