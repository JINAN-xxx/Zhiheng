import request from '@/utils/request'

const prefix = '/ai/knowledge'

export function listPlatformModels(query) {
  return request({ url: '/ai/models', method: 'get', params: query })
}

export const listAvailableKnowledgeBases = () => request({ url: `${prefix}/available`, method: 'get' })

export { isCallablePlatformModel, normalizeApplicationModelPage } from '../utils/application.js'
export { normalizeKnowledgeDatabases } from '../utils/knowledge.js'
export { collectAllPages } from '../utils/modelEngine.js'
