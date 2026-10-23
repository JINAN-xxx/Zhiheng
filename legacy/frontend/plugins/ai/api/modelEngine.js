import request from '@/utils/request'
import { buildSafeResourcePath, buildSecretBodyRequest } from '../utils/modelEngine.js'

const enginePath = (resource, resourceId = '', suffix = '') => {
  const basePath = `/ai/model-engine/${resource}`
  return resourceId ? buildSafeResourcePath(basePath, resourceId, suffix.replace(/^\//, '')) : basePath
}

export function listEngineProviders() {
  return request({ url: enginePath('providers'), method: 'get' })
}

export function listEngineCredentials() {
  return request({ url: enginePath('credentials'), method: 'get' })
}

export function createEngineCredential(data) {
  return request({ url: enginePath('credentials'), method: 'post', ...buildSecretBodyRequest(data) })
}

export function rotateEngineCredential(credentialName, data) {
  return request({
    url: enginePath('credentials', credentialName, '/values'),
    method: 'put',
    ...buildSecretBodyRequest(data),
  })
}

export function deleteEngineCredential(credentialName) {
  return request({ url: enginePath('credentials', credentialName), method: 'delete' })
}

export function listEngineDeployments(params) {
  return request({ url: enginePath('deployments'), method: 'get', params })
}

export function getEngineDeployment(deploymentId) {
  return request({ url: enginePath('deployments', deploymentId), method: 'get' })
}

export function createEngineDeployment(data) {
  return request({ url: enginePath('deployments'), method: 'post', data, headers: { repeatSubmit: false } })
}

export function updateEngineDeployment(deploymentId, data) {
  return request({ url: enginePath('deployments', deploymentId), method: 'patch', data })
}

export function deleteEngineDeployment(deploymentId) {
  return request({ url: enginePath('deployments', deploymentId), method: 'delete' })
}

export function testEngineConnection(data) {
  // The gateway can spend up to 60 seconds probing a cold or reasoning model.
  return request({ url: enginePath('test-connection'), method: 'post', timeout: 120000, ...buildSecretBodyRequest(data) })
}

export function getEngineRuntimeStatus() {
  return request({ url: enginePath('status'), method: 'get' })
}
