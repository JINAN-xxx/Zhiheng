import { decodeVoid, http } from '../../../shared/http.ts'
import { followRun, parseRunSnapshot } from '../../../shared/stream.ts'
import type { RunSnapshot, RunStatus } from '../../../shared/stream.ts'
import {
  boolean,
  list,
  nullableNumber,
  number,
  object,
  string
} from '../../../shared/validation.ts'

export type WorkStatus = 'active' | 'completed' | 'archived'
export interface WorkAgent {
  agentId: string
  name: string
  description: string
  publishedVersion: number
}
export interface WorkMember {
  agentId: string
  agentNameSnapshot: string
  publishedVersion: number
  latestPublishedVersion: number | null
  available: boolean
  removedAt: string | null
}
export interface WorkMessage {
  messageId: string
  clientRequestId: string
  role: 'user' | 'assistant'
  content: string
  reasoningContent: string
  runId: string | null
  runStatus: RunStatus
  usage: RunSnapshot['usage'] | null
  errorSnapshot: RunSnapshot['error']
  agentId: string | null
  agentNameSnapshot: string | null
  publishedVersion: number | null
  sequence: number
  createdAt: string
}
export interface WorkSummary {
  taskId: string
  ownerId: string
  title: string
  goal: string
  status: WorkStatus
  version: number
  currentRunId: string | null
  activeAgentId: string | null
  createClientRequestId: string
  createdAt: string
  updatedAt: string
}
export interface WorkTask extends WorkSummary {
  members: WorkMember[]
  messages: WorkMessage[]
}
export interface WorkFilters {
  keyword: string
  status: WorkStatus | ''
  pageNum: number
  pageSize: number
}
const nullableString = (value: unknown) => (value === null ? null : string(value))
function parseStatus(value: unknown): WorkStatus {
  if (value !== 'active' && value !== 'completed' && value !== 'archived')
    throw new Error('工作状态无效')
  return value
}
function parseRunStatus(value: unknown): RunStatus {
  if (
    ![
      'pending',
      'running',
      'cancel_requested',
      'completed',
      'failed',
      'cancelled',
      'interrupted'
    ].includes(string(value))
  )
    throw new Error('工作消息状态无效')
  return value as RunStatus
}
function parseSummary(value: unknown): WorkSummary {
  const data = object(value)
  return {
    taskId: string(data.id),
    ownerId: string(data.owner_id),
    title: string(data.title),
    goal: string(data.goal),
    status: parseStatus(data.status),
    version: number(data.version),
    currentRunId: nullableString(data.current_run_id),
    activeAgentId: nullableString(data.active_agent_id),
    createClientRequestId: string(data.create_client_request_id),
    createdAt: string(data.created_at),
    updatedAt: string(data.updated_at)
  }
}
function parseMessage(value: unknown): WorkMessage {
  const data = object(value)
  if (data.role !== 'user' && data.role !== 'assistant') throw new Error('工作消息角色无效')
  const error = data.error === null ? null : object(data.error)
  const usage = data.usage === null ? null : object(data.usage)
  if (usage && usage.status !== 'pending' && usage.status !== 'known' && usage.status !== 'unknown')
    throw new Error('工作用量无效')
  return {
    messageId: string(data.id),
    clientRequestId: string(data.client_request_id),
    role: data.role,
    content: string(data.content),
    reasoningContent: string(data.reasoning),
    runId: nullableString(data.run_id),
    runStatus: parseRunStatus(data.status),
    usage:
      usage === null
        ? null
        : {
            input_tokens: nullableNumber(usage.input_tokens),
            output_tokens: nullableNumber(usage.output_tokens),
            status: usage.status as RunSnapshot['usage']['status']
          },
    errorSnapshot:
      error === null ? null : { code: string(error.code), message: string(error.message) },
    agentId: nullableString(data.agent_id),
    agentNameSnapshot: nullableString(data.agent_name_snapshot),
    publishedVersion: nullableNumber(data.published_version),
    sequence: number(data.sequence),
    createdAt: string(data.created_at)
  }
}
function parseTask(value: unknown): WorkTask {
  const data = object(value)
  return {
    ...parseSummary(data),
    members: list((value) => {
      const item = object(value)
      return {
        agentId: string(item.agent_id),
        agentNameSnapshot: string(item.agent_name_snapshot),
        publishedVersion: number(item.published_version),
        latestPublishedVersion: nullableNumber(item.latest_published_version),
        available: boolean(item.available),
        removedAt: nullableString(item.removed_at)
      }
    })(data.members),
    messages: list(parseMessage)(data.messages)
  }
}
const taskPath = (id: string) => `/work/tasks/${encodeURIComponent(id)}`
const runPath = (taskId: string, runId: string) =>
  `${taskPath(taskId)}/runs/${encodeURIComponent(runId)}`
export const listTasks = (query: WorkFilters, signal: AbortSignal) =>
  http.request(
    `/work/tasks?${new URLSearchParams({ search: query.keyword, page: String(query.pageNum), page_size: String(query.pageSize), ...(query.status ? { status: query.status } : {}) })}`,
    {
      signal,
      decode: (value) => {
        const data = object(value)
        return { rows: list(parseSummary)(data.items), total: number(data.total) }
      }
    }
  )
export const availableAgents = (taskId: string | null, signal: AbortSignal) =>
  http.request(`/work/agents/available${taskId ? `?task_id=${encodeURIComponent(taskId)}` : ''}`, {
    signal,
    decode: list((value): WorkAgent => {
      const item = object(value)
      return {
        agentId: string(item.id),
        name: string(item.name),
        description: string(item.description),
        publishedVersion: number(item.published_version)
      }
    })
  })
export const getTask = (id: string, signal: AbortSignal) =>
  http.request(taskPath(id), { signal, decode: parseTask })
export const createTask = (
  body: { goal: string; agent_ids: string[]; active_agent_id: string; client_request_id: string },
  signal: AbortSignal
) => http.request('/work/tasks', { method: 'POST', body, signal, decode: parseTask })
export const updateTask = (
  id: string,
  body: { expected_version: number; title?: string; goal?: string; status?: WorkStatus },
  signal: AbortSignal
) => http.request(taskPath(id), { method: 'PATCH', body, signal, decode: parseTask })
export const deleteTask = (id: string, version: number, signal: AbortSignal) =>
  http.request(`${taskPath(id)}?expected_version=${version}`, {
    method: 'DELETE',
    signal,
    decode: decodeVoid
  })
export const addMember = (id: string, agentId: string, version: number, signal: AbortSignal) =>
  http.request(`${taskPath(id)}/members`, {
    method: 'POST',
    body: { agent_id: agentId, expected_version: version },
    signal,
    decode: parseTask
  })
export const upgradeMember = (
  id: string,
  agentId: string,
  version: number,
  publication: number,
  signal: AbortSignal
) =>
  http.request(`${taskPath(id)}/members/${encodeURIComponent(agentId)}`, {
    method: 'PATCH',
    body: { expected_version: version, expected_published_version: publication },
    signal,
    decode: parseTask
  })
export const removeMember = (id: string, agentId: string, version: number, signal: AbortSignal) =>
  http.request(
    `${taskPath(id)}/members/${encodeURIComponent(agentId)}?expected_version=${version}`,
    { method: 'DELETE', signal, decode: parseTask }
  )
export const admitTurn = (
  id: string,
  body: { agent_id: string; client_request_id: string; message: string; expected_version: number },
  signal: AbortSignal
) =>
  http.request(`${taskPath(id)}/runs`, { method: 'POST', body, signal, decode: parseRunSnapshot })
export const getWorkRun = (taskId: string, runId: string, signal: AbortSignal) =>
  http.request(runPath(taskId, runId), { signal, decode: parseRunSnapshot })
export const cancelWorkRun = (taskId: string, runId: string, signal: AbortSignal) =>
  http.request(`${runPath(taskId, runId)}/cancel`, {
    method: 'POST',
    body: {},
    signal,
    decode: parseRunSnapshot
  })
export const observeWorkRun = (
  taskId: string,
  initial: RunSnapshot,
  update: (run: RunSnapshot) => void,
  signal: AbortSignal
) =>
  followRun(
    {
      get: (signal) => getWorkRun(taskId, initial.id, signal),
      open: (signal) => http.open(`${runPath(taskId, initial.id)}/events`, { signal })
    },
    initial,
    update,
    signal
  )
