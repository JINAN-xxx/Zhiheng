import request from '@/utils/request'

const BASE_URL = '/agent_extension/agents'
const path = (agentId) => `${BASE_URL}/${encodeURIComponent(agentId)}`

export function listAgents(query) {
  return request({ url: BASE_URL, method: 'get', params: query })
}

export function createAgent(data) {
  return request({ url: BASE_URL, method: 'post', data })
}

export function getAgent(agentId) {
  return request({ url: path(agentId), method: 'get' })
}

export function updateAgent(agentId, data) {
  return request({ url: path(agentId), method: 'put', data })
}

export function deleteAgent(agentId) {
  return request({ url: path(agentId), method: 'delete' })
}

export function publishAgent(agentId, expectedVersion) {
  return request({ url: `${path(agentId)}/publish`, method: 'post', data: { expectedVersion } })
}

export function unpublishAgent(agentId, expectedVersion) {
  return request({ url: `${path(agentId)}/unpublish`, method: 'post', data: { expectedVersion } })
}

export function previewAgent(agentId, data, signal) {
  return request({ url: `${path(agentId)}/preview`, method: 'post', data, signal, timeout: 120000, headers: { repeatSubmit: false } })
}
