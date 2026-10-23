import request from '@/utils/request'
import { getToken } from '@/utils/auth'
import { normalizeErrorMessage } from '@/utils/errorMessage'
import { parsePlatformSSE } from '@/utils/platformSse.js'
import {
  buildCreateWorkTaskPayload,
  buildRunWorkTaskPayload,
  buildUpdateWorkTaskPayload,
} from '../utils/workTask.js'

const PREFIX = '/agent_extension/work-tasks'

export const listWorkTasks = (params) => request({ url: PREFIX, method: 'get', params })
export const listAvailableWorkAgents = () => request({ url: `${PREFIX}/available-agents`, method: 'get' })
export const getWorkTask = (taskId) => request({ url: `${PREFIX}/${taskId}`, method: 'get' })
export const deleteWorkTask = (taskId) => request({ url: `${PREFIX}/${taskId}`, method: 'delete' })

export const createWorkTask = (payload) => request({
  url: PREFIX,
  method: 'post',
  data: buildCreateWorkTaskPayload(payload),
  headers: { repeatSubmit: false },
})

export const updateWorkTask = (taskId, payload) => request({
  url: `${PREFIX}/${taskId}`,
  method: 'put',
  data: buildUpdateWorkTaskPayload(payload),
})

export const addWorkTaskMember = (taskId, { agentId, expectedVersion }) => request({
  url: `${PREFIX}/${taskId}/members`, method: 'post', data: { agentId, expectedVersion },
})

export const updateWorkTaskMemberVersion = (
  taskId,
  agentId,
  { expectedVersion, expectedPublishedVersion },
) => request({
  url: `${PREFIX}/${taskId}/members/${agentId}`,
  method: 'put',
  data: { expectedVersion, expectedPublishedVersion },
})

export const removeWorkTaskMember = (taskId, agentId, expectedVersion) => request({
  url: `${PREFIX}/${taskId}/members/${agentId}`,
  method: 'delete',
  params: { expectedVersion },
})

export const cancelWorkTaskRun = (taskId, runId) => request({
  url: `${PREFIX}/${taskId}/runs/${runId}/cancel`, method: 'post', data: undefined,
})

async function readErrorResponse(response) {
  let payload = null
  try {
    payload = await response.json()
  } catch {
    // Proxies may return HTML/plain text. Keep the public fallback stable.
  }
  const error = new Error(normalizeErrorMessage(payload?.msg ?? payload?.detail, '工作执行请求失败'))
  error.code = payload?.code || payload?.detail?.code || 'WORK_RUN_FAILED'
  error.status = response.status
  throw error
}

export async function createWorkTaskRunStream(taskId, payload, signal) {
  const response = await fetch(`${import.meta.env.VITE_APP_BASE_API}${PREFIX}/${taskId}/runs`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify(buildRunWorkTaskPayload(payload)),
    signal,
  })
  const contentType = response.headers.get('content-type') || ''
  if (!response.ok || !contentType.toLowerCase().includes('text/event-stream')) {
    await readErrorResponse(response)
  }
  if (!response.body) throw new Error('工作执行响应为空')
  return parsePlatformSSE(response.body)
}
