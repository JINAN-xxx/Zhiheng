import request from '@/utils/request'
import { buildApplicationPath } from '../utils/application.js'

export function getApplication(applicationId) {
  return request({
    url: buildApplicationPath(applicationId),
    method: 'get',
    acceptNotFound: true,
    validateStatus: (status) => (status >= 200 && status < 300) || status === 404,
  })
}

export function updateApplication(applicationId, data) {
  return request({ url: buildApplicationPath(applicationId), method: 'patch', data })
}

export function disableApplication(applicationId) {
  return request({ url: buildApplicationPath(applicationId, 'disable'), method: 'post' })
}

export function enableApplication(applicationId) {
  return request({ url: buildApplicationPath(applicationId, 'enable'), method: 'post' })
}

export function deleteApplication(applicationId) {
  return request({ url: buildApplicationPath(applicationId), method: 'delete' })
}

export function listApplicationUsage(applicationId, query) {
  return request({
    url: buildApplicationPath(applicationId, 'usage'),
    method: 'get',
    params: query,
  })
}

export { listApplications, createApplication } from '../public/applications.js'

export { listDeveloperKeys, createDeveloperKey, rotateDeveloperKey, revokeDeveloperKey } from '../public/developerKeys.js'
