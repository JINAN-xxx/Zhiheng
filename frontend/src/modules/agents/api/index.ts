import { decodeVoid, http } from '../../../shared/http.ts'
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
  knowledgeTopK: number
  applicationId: string | null
  temperature: number
  maxTokens: number
}
export interface Agent extends AgentConfig {
  agentId: string
  ownerId: string
  version: number
  plazaVersion?: number | null
  plazaDepartmentId?: string | null
  status: 'disabled' | 'enabled'
  updatedAt?: string
}
export interface AgentPayload {
  client_request_id?: string
  name: string
  description: string
  model_id: string
  system_prompt: string
  welcome_message: string
  skill_slugs: string[]
  knowledge_base_ids: string[]
  knowledge_top_k: number
  application_id: string | null
  temperature: number
  max_tokens: number
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
    knowledgeTopK: data.knowledge_top_k === undefined ? 8 : number(data.knowledge_top_k),
    applicationId: data.application_id === null ? null : string(data.application_id),
    temperature: number(data.temperature),
    maxTokens: number(data.max_tokens)
  }
}
function parseAgent(value: unknown): Agent {
  const data = object(value)
  if (data.status !== 'disabled' && data.status !== 'enabled') throw new Error('智能体状态无效')
  return {
    ...parseConfig(data),
    agentId: string(data.id),
    ownerId: string(data.owner_id),
    version: number(data.version),
    plazaVersion: data.plaza_version == null ? null : number(data.plaza_version),
    plazaDepartmentId: data.plaza_department_id == null ? null : string(data.plaza_department_id),
    status: data.status,
    ...(data.updated_at === undefined ? {} : { updatedAt: string(data.updated_at) })
  }
}
const path = (id: string) => `/agents/${encodeURIComponent(id)}`
export interface AgentKnowledgePolicy {
  modelId: string
  source: 'knowledge_base' | 'platform_default' | 'mixed_platform_default'
}
export const getAgentKnowledgePolicy = (baseIds: string[], signal: AbortSignal) =>
  http.request('/agents/knowledge-policy', {
    method: 'POST',
    body: { knowledge_base_ids: baseIds },
    signal,
    decode: (value): AgentKnowledgePolicy => {
      const data = object(value)
      if (
        data.source !== 'knowledge_base' &&
        data.source !== 'platform_default' &&
        data.source !== 'mixed_platform_default'
      )
        throw new Error('重排策略无效')
      return {
        modelId: string(data.model_id),
        source: data.source
      }
    }
  })
export const listAgents = (
  query: { keyword: string; pageNum: number; pageSize: number; status?: Agent['status'] },
  signal: AbortSignal
) =>
  http.request(
    `/agents?${new URLSearchParams({ search: query.keyword, page: String(query.pageNum), page_size: String(query.pageSize), ...(query.status ? { status: query.status } : {}) })}`,
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

export interface PlazaAssistant {
  agentId: string
  name: string
  description: string
  welcomeMessage: string
  plazaVersion?: number | null
  canStart?: boolean
}

function parsePlazaAssistant(value: unknown): PlazaAssistant {
  const data = object(value)
  return {
    agentId: string(data.id),
    name: string(data.name),
    description: string(data.description),
    welcomeMessage: string(data.welcome_message),
    plazaVersion: data.plaza_version == null ? null : number(data.plaza_version),
    canStart: data.can_start !== false
  }
}

export const listPlazaAssistants = (
  query: { keyword: string; page: number; pageSize: number },
  signal: AbortSignal
) =>
  http.request(
    `/agents/plaza?${new URLSearchParams({ search: query.keyword, page: String(query.page), page_size: String(query.pageSize) })}`,
    {
      signal,
      decode: (value) => {
        const data = object(value)
        return { rows: list(parsePlazaAssistant)(data.items), total: number(data.total) }
      }
    }
  )
export const getPlazaAssistant = (id: string, signal: AbortSignal) =>
  http.request(`/agents/plaza/${encodeURIComponent(id)}`, { signal, decode: parsePlazaAssistant })
export const listPlazaDepartments = (signal: AbortSignal) =>
  http.request('/agents/plaza/departments', {
    signal,
    decode: list((value) => {
      const data = object(value)
      return { id: string(data.id), name: string(data.name) }
    })
  })
export const publishAssistant = (
  agent: Agent,
  scope: { department_id: string | null; allow_knowledge: boolean },
  signal: AbortSignal
) =>
  http.request(`${path(agent.agentId)}/plaza`, {
    method: 'PUT',
    body: { expected_version: agent.version, ...scope },
    signal,
    decode: parseAgent
  })
export const unpublishAssistant = (agentId: string, version: number, signal: AbortSignal) =>
  http.request(`${path(agentId)}/plaza`, {
    method: 'DELETE',
    body: { expected_version: version },
    signal,
    decode: decodeVoid
  })
export const createAgent = (body: AgentPayload, signal: AbortSignal) =>
  http.request('/agents', { method: 'POST', body, signal, decode: parseAgent })
export const updateAgent = (
  id: string,
  body: AgentPayload & { expected_version: number },
  signal: AbortSignal
) => http.request(path(id), { method: 'PATCH', body, signal, decode: parseAgent })
export const enableAgent = (id: string, version: number, signal: AbortSignal) =>
  http.request(`${path(id)}/enable`, {
    method: 'POST',
    body: { expected_version: version },
    signal,
    decode: parseAgent
  })
export const disableAgent = (id: string, version: number, signal: AbortSignal) =>
  http.request(`${path(id)}/disable`, {
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
  createId: () => string
): PreviewIntent {
  if (
    previous &&
    previous.agentId === agentId &&
    JSON.stringify(previous.sourceMessages) === JSON.stringify(messages)
  )
    return previous
  return {
    agentId,
    messages: selectPreviewMessages(messages),
    sourceMessages: messages.map((message) => ({ ...message })),
    clientRequestId: createId()
  }
}
