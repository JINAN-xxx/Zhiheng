import { http } from '../../../shared/http.ts'
import { list, number, object, string } from '../../../shared/validation.ts'
import { parseRunAdmission } from '../../knowledge/public.ts'
import { selectPreviewMessages } from '../history.ts'
import type { PreviewHistoryMessage } from '../history.ts'
export interface AgentConfig {
  name: string
  description: string
  modelId: string
  systemPrompt: string
  welcomeMessage: string
  skillSlugs: string[]
  knowledgeBaseIds: string[]
  applicationId: string | null
  temperature: number
  maxTokens: number
  historyRounds: number
}
export interface Agent extends AgentConfig {
  agentId: string
  ownerId: string
  version: number
  status: 'draft' | 'published'
  publishedVersion: number | null
  publishedConfig: AgentConfig | null
}
export interface AgentPayload {
  name: string
  description: string
  model_id: string
  system_prompt: string
  welcome_message: string
  skill_slugs: string[]
  knowledge_base_ids: string[]
  application_id: string | null
  temperature: number
  max_tokens: number
  history_rounds: number
}
function parseConfig(value: unknown): AgentConfig {
  const data = object(value)
  return {
    name: string(data.name),
    description: string(data.description),
    modelId: string(data.model_id),
    systemPrompt: string(data.system_prompt),
    welcomeMessage: string(data.welcome_message),
    skillSlugs: list(string)(data.skill_slugs),
    knowledgeBaseIds: list(string)(data.knowledge_base_ids),
    applicationId: data.application_id === null ? null : string(data.application_id),
    temperature: number(data.temperature),
    maxTokens: number(data.max_tokens),
    historyRounds: data.history_rounds === undefined ? 20 : number(data.history_rounds)
  }
}
function parseAgent(value: unknown): Agent {
  const data = object(value)
  if (data.status !== 'draft' && data.status !== 'published') throw new Error('智能体状态无效')
  return {
    ...parseConfig(data),
    agentId: string(data.id),
    ownerId: string(data.owner_id),
    version: number(data.version),
    status: data.status,
    publishedVersion: data.published_version === null ? null : number(data.published_version),
    publishedConfig: data.published_config === null ? null : parseConfig(data.published_config)
  }
}
const path = (id: string) => `/agents/${encodeURIComponent(id)}`
export const listAgents = (
  query: { keyword: string; pageNum: number; pageSize: number },
  signal: AbortSignal
) =>
  http.request(
    `/agents?${new URLSearchParams({ search: query.keyword, page: String(query.pageNum), page_size: String(query.pageSize) })}`,
    {
      signal,
      decode: (value) => {
        const data = object(value)
        return { rows: list(parseAgent)(data.items), total: number(data.total) }
      }
    }
  )
export const getAgent = (id: string, signal: AbortSignal) =>
  http.request(path(id), { signal, decode: parseAgent })
export const createAgent = (body: AgentPayload, signal: AbortSignal) =>
  http.request('/agents', { method: 'POST', body, signal, decode: parseAgent })
export const updateAgent = (
  id: string,
  body: AgentPayload & { expected_version: number },
  signal: AbortSignal
) => http.request(path(id), { method: 'PATCH', body, signal, decode: parseAgent })
export const publishAgent = (id: string, version: number, signal: AbortSignal) =>
  http.request(`${path(id)}/publish`, {
    method: 'POST',
    body: { expected_version: version },
    signal,
    decode: parseAgent
  })
export const unpublishAgent = (id: string, version: number, signal: AbortSignal) =>
  http.request(`${path(id)}/unpublish`, {
    method: 'POST',
    body: { expected_version: version },
    signal,
    decode: parseAgent
  })
export const deleteAgent = (id: string, signal: AbortSignal) =>
  http.request(path(id), { method: 'DELETE', signal, decode: parseAgent })
export interface PreviewMessage {
  role: 'user' | 'assistant'
  content: string
}
export const admitPreview = (
  id: string,
  body: { messages: PreviewMessage[]; client_request_id: string },
  signal: AbortSignal
) =>
  http.request(`${path(id)}/preview`, { method: 'POST', body, signal, decode: parseRunAdmission })

export interface PreviewIntent {
  agentId: string
  messages: PreviewMessage[]
  clientRequestId: string
  sourceMessages: PreviewHistoryMessage[]
}
export function nextPreviewIntent(
  previous: PreviewIntent | null,
  agentId: string,
  messages: PreviewHistoryMessage[],
  createId: () => string,
  historyRounds = 20
): PreviewIntent {
  if (
    previous &&
    previous.agentId === agentId &&
    JSON.stringify(previous.sourceMessages) === JSON.stringify(messages)
  )
    return previous
  return {
    agentId,
    messages: selectPreviewMessages(messages, historyRounds),
    sourceMessages: messages.map((message) => ({ ...message })),
    clientRequestId: createId()
  }
}

export { getRun, cancelRun, observeRun } from '../../../shared/runs.ts'
