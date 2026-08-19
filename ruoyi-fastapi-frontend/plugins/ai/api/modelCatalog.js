import request from '@/utils/request'
import { buildSafeResourcePath } from '../utils/modelEngine.js'

const modelPath = (modelId, suffix = '') => buildSafeResourcePath('/ai/models', modelId, suffix.replace(/^\//, ''))

export function listPlatformModels(query) {
  return request({ url: '/ai/models', method: 'get', params: query })
}

export function getPlatformModel(modelId) {
  return request({ url: modelPath(modelId), method: 'get' })
}

export function createPlatformModel(data) {
  return request({ url: '/ai/models', method: 'post', data, headers: { repeatSubmit: false } })
}

export function updatePlatformModel(modelId, data) {
  return request({ url: modelPath(modelId), method: 'patch', data })
}

export function savePlatformModelBinding(modelId, data) {
  return request({ url: modelPath(modelId, '/binding'), method: 'put', data, headers: { repeatSubmit: false } })
}

export function publishPlatformModel(modelId) {
  return request({ url: modelPath(modelId, '/publish'), method: 'post', headers: { repeatSubmit: false } })
}

export function disablePlatformModel(modelId) {
  return request({ url: modelPath(modelId, '/disable'), method: 'post' })
}

export function deletePlatformModel(modelId) {
  return request({ url: modelPath(modelId), method: 'delete' })
}
