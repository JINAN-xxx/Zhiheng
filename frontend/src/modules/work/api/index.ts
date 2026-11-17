import { decodeVoid, http } from '../../../shared/http.ts'
import { followRun, parseRunSnapshot } from '../../../shared/stream.ts'
import type { RunSnapshot, RunStatus } from '../../../shared/stream.ts'
import { parseKnowledgeHit } from '../../knowledge/public.ts'
import type { KnowledgeHit } from '../../knowledge/public.ts'
import {
  boolean,
  list,
  nullableNumber,
  number,
  object,
  string
} from '../../../shared/validation.ts'

export type WorkStatus = 'active' | 'completed' | 'archived'
export type WorkExecutionState =
  | 'idle'
  | 'queued'
  | 'running'
  | 'waiting_input'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled'
export interface WorkMember {
  agentId: string
  agentNameSnapshot: string
  agentRevision: number
  latestAgentRevision: number | null
  available: boolean
  removedAt: string | null
}
export interface WorkConfiguration {
  modelId: string
  skillSlugs: string[]
  allowCodeExecution: boolean
}
export interface WorkOptions {
  models: { id: string; name: string; maxOutputTokens: number }[]
  skills: { slug: string; name: string; description: string; version: number | string }[]
  canExecuteCode: boolean
  sandboxAvailable: boolean
}
export interface WorkInput {
  fileId: string
  name: string
  version: number
  size: number
  mime: string
  sha256: string
  aclVersion: number
  fileVersion: number
  createdAt: string
}
export interface WorkStep {
  id: string
  runId: string
  actionId: string
  kind: string
  name: string
  status: string
  summary: string
  details: Record<string, unknown>
  result: Record<string, unknown> | null
  createdAt: string
  updatedAt: string
}
export interface WorkArtifact {
  id: string
  runId: string
  fileId: string
  name: string
  logicalPath: string
  version: number
  size: number
  mime: string
  sha256: string
  validation: Record<string, unknown>
  previewFileId: string | null
  createdAt: string
}
export interface WorkPlanItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'
}
export interface WorkMessage {
  knowledgeSources: KnowledgeHit[]
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
  agentRevision: number | null
  sequence: number
  createdAt: string
}
export interface WorkSummary {
  taskId: string
  ownerId: string
  title: string
  goal: string
  status: WorkStatus
  mode: 'legacy' | 'agent'
  modelId: string | null
  executionState: WorkExecutionState
  version: number
  currentRunId: string | null
  preparationId: string | null
  activeAgentId: string | null
  createClientRequestId: string
  createdAt: string
  updatedAt: string
}
export interface WorkTask extends WorkSummary {
  configuration: WorkConfiguration | null
  inputs: WorkInput[]
  steps: WorkStep[]
  artifacts: WorkArtifact[]
  plan: WorkPlanItem[]
  members: WorkMember[]
  messages: WorkMessage[]
}
export interface WorkFilters {
  keyword: string
  status: WorkStatus | ''
  pageNum: number
  pageSize: number
}
export interface CreateWorkBody {
  goal: string
  model_id: string
  skill_slugs: string[]
  file_ids: string[]
  allow_code_execution: boolean
  client_request_id: string
}
export interface UpdateWorkBody {
  expected_version: number
  title?: string
  goal?: string
  status?: WorkStatus
  model_id?: string
  skill_slugs?: string[]
  allow_code_execution?: boolean
}
const nullableString = (value: unknown) => (value == null ? null : string(value))
function parseStatus(value: unknown): WorkStatus {
  if (value !== 'active' && value !== 'completed' && value !== 'archived')
    throw new Error('工作状态无效')
  return value
}
function parseExecutionState(value: unknown): WorkExecutionState {
  if (
    ![
      'idle',
      'queued',
      'running',
      'waiting_input',
      'paused',
      'completed',
      'failed',
      'cancelled'
    ].includes(string(value))
  )
    throw new Error('任务执行状态无效')
  return value as WorkExecutionState
}
function parseSummary(value: unknown): WorkSummary {
  const data = object(value)
  if (data.mode !== undefined && data.mode !== 'legacy' && data.mode !== 'agent')
    throw new Error('工作任务类型无效')
  return {
    taskId: string(data.id),
    ownerId: string(data.owner_id),
    title: string(data.title),
    goal: string(data.goal),
    status: parseStatus(data.status),
    mode: data.mode === 'agent' ? 'agent' : 'legacy',
    modelId: nullableString(data.model_id),
    executionState: parseExecutionState(data.execution_state ?? 'idle'),
    version: number(data.version),
    currentRunId: nullableString(data.current_run_id),
    preparationId: nullableString(data.preparation_id),
    activeAgentId: nullableString(data.active_agent_id),
    createClientRequestId: string(data.create_client_request_id),
    createdAt: string(data.created_at),
    updatedAt: string(data.updated_at)
  }
}
function parseMessage(value: unknown): WorkMessage {
  const data = object(value)
  if (data.role !== 'user' && data.role !== 'assistant') throw new Error('工作消息角色无效')
  const error = data.error == null ? null : object(data.error)
  const usage = data.usage == null ? null : object(data.usage)
  const status = string(data.status)
  if (
    ![
      'pending',
      'running',
      'cancel_requested',
      'completed',
      'failed',
      'cancelled',
      'interrupted'
    ].includes(status)
  )
    throw new Error('工作消息状态无效')
  if (usage && !['pending', 'known', 'unknown'].includes(string(usage.status)))
    throw new Error('工作用量无效')
  return {
    knowledgeSources:
      data.knowledge_sources == null ? [] : list(parseKnowledgeHit)(data.knowledge_sources),
    messageId: string(data.id),
    clientRequestId: string(data.client_request_id),
    role: data.role,
    content: string(data.content),
    reasoningContent: typeof data.reasoning === 'string' ? data.reasoning : '',
    runId: nullableString(data.run_id),
    runStatus: status as RunStatus,
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
    agentRevision: data.agent_revision == null ? null : number(data.agent_revision),
    sequence: number(data.sequence),
    createdAt: string(data.created_at)
  }
}
function parseConfiguration(value: unknown): WorkConfiguration {
  const data = object(value)
  return {
    modelId: string(data.model_id),
    skillSlugs: list(string)(data.skill_slugs),
    allowCodeExecution: boolean(data.allow_code_execution)
  }
}
function parseInput(value: unknown): WorkInput {
  const data = object(value)
  return {
    fileId: string(data.file_id),
    name: string(data.name),
    version: number(data.version),
    size: number(data.size),
    mime: string(data.mime),
    sha256: string(data.sha256),
    aclVersion: number(data.acl_version),
    fileVersion: number(data.file_version),
    createdAt: string(data.created_at)
  }
}
function parseStep(value: unknown): WorkStep {
  const data = object(value)
  return {
    id: string(data.id),
    runId: string(data.run_id),
    actionId: string(data.action_id),
    kind: string(data.kind),
    name: string(data.name),
    status: string(data.status),
    summary: string(data.summary),
    details: object(data.details),
    result: data.result == null ? null : object(data.result),
    createdAt: string(data.created_at),
    updatedAt: string(data.updated_at)
  }
}
function parseArtifact(value: unknown): WorkArtifact {
  const data = object(value)
  return {
    id: string(data.id),
    runId: string(data.run_id),
    fileId: string(data.file_id),
    name: string(data.name),
    logicalPath: string(data.logical_path),
    version: number(data.version),
    size: number(data.size),
    mime: string(data.mime),
    sha256: string(data.sha256),
    validation: object(data.validation),
    previewFileId: nullableString(data.preview_file_id),
    createdAt: string(data.created_at)
  }
}
function parseTask(value: unknown): WorkTask {
  const data = object(value)
  return {
    ...parseSummary(data),
    configuration: data.configuration == null ? null : parseConfiguration(data.configuration),
    inputs: list(parseInput)(data.inputs ?? []),
    steps: list(parseStep)(data.steps ?? []),
    artifacts: list(parseArtifact)(data.artifacts ?? []),
    plan: list((value): WorkPlanItem => {
      const item = object(value)
      if (!['pending', 'in_progress', 'completed'].includes(string(item.status)))
        throw new Error('任务计划状态无效')
      return { content: string(item.content), status: item.status as WorkPlanItem['status'] }
    })(data.plan ?? []),
    members: list((value): WorkMember => {
      const item = object(value)
      return {
        agentId: string(item.agent_id),
        agentNameSnapshot: string(item.agent_name_snapshot),
        agentRevision: number(item.agent_revision),
        latestAgentRevision:
          item.latest_agent_revision == null ? null : number(item.latest_agent_revision),
        available: boolean(item.available),
        removedAt: nullableString(item.removed_at)
      }
    })(data.members ?? []),
    messages: list(parseMessage)(data.messages)
  }
}
const taskPath = (id: string) => `/work/tasks/${encodeURIComponent(id)}`
const runPath = (taskId: string, runId: string) =>
  `${taskPath(taskId)}/runs/${encodeURIComponent(runId)}`
export const getWorkOptions = (signal: AbortSignal) =>
  http.request('/work/options', {
    signal,
    decode: (value): WorkOptions => {
      const data = object(value)
      return {
        models: list((value) => {
          const item = object(value)
          return {
            id: string(item.id),
            name: string(item.name),
            maxOutputTokens: number(item.max_output_tokens)
          }
        })(data.models),
        skills: list((value) => {
          const item = object(value)
          if (typeof item.version !== 'number' && typeof item.version !== 'string')
            throw new Error('技能版本无效')
          return {
            slug: string(item.slug),
            name: string(item.name),
            description: string(item.description),
            version: item.version
          }
        })(data.skills),
        canExecuteCode: boolean(data.can_execute_code),
        sandboxAvailable: boolean(data.sandbox_available)
      }
    }
  })
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
export const getTask = (id: string, signal: AbortSignal) =>
  http.request(taskPath(id), { signal, decode: parseTask })
export const createTask = (body: CreateWorkBody, signal: AbortSignal) =>
  http.request('/work/tasks', { method: 'POST', body, signal, decode: parseTask })
export const updateTask = (id: string, body: UpdateWorkBody, signal: AbortSignal) =>
  http.request(taskPath(id), { method: 'PATCH', body, signal, decode: parseTask })
export const deleteTask = (id: string, version: number, signal: AbortSignal) =>
  http.request(`${taskPath(id)}?expected_version=${version}`, {
    method: 'DELETE',
    signal,
    decode: decodeVoid
  })
export const admitTurn = (
  id: string,
  body: {
    client_request_id: string
    message: string
    expected_version: number
    file_ids: string[]
    resume: boolean
  },
  signal: AbortSignal
) =>
  http.request(`${taskPath(id)}/runs`, { method: 'POST', body, signal, decode: parseRunSnapshot })
export const uploadTaskFile = (taskId: string, file: File, signal: AbortSignal) =>
  http.request(`${taskPath(taskId)}/files?${new URLSearchParams({ filename: file.name })}`, {
    method: 'POST',
    body: file,
    signal,
    decode: parseTask
  })
export async function downloadInput(
  taskId: string,
  fileId: string,
  signal: AbortSignal
): Promise<Blob> {
  return (
    await http.open(`${taskPath(taskId)}/inputs/${encodeURIComponent(fileId)}/download`, { signal })
  ).blob()
}
export async function downloadArtifact(
  taskId: string,
  artifactId: string,
  signal: AbortSignal,
  preview = false
): Promise<Blob> {
  return (
    await http.open(
      `${taskPath(taskId)}/artifacts/${encodeURIComponent(artifactId)}/${preview ? 'preview' : 'download'}`,
      { signal }
    )
  ).blob()
}
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
