import request from '@/utils/request'

export function listApplications(query) {
  return request({ url: '/ai/applications', method: 'get', params: query })
}

export function createApplication(data) {
  return request({ url: '/ai/applications', method: 'post', data })
}

export { normalizeApplication, normalizeApplicationPage } from '../utils/application.js'
