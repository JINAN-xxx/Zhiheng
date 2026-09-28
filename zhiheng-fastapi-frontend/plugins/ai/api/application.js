import request from '@/utils/request'
import { buildApplicationPath } from '../utils/application.js'

export function listApplications(query) {
  return request({ url: '/ai/applications', method: 'get', params: query })
}

export function getApplication(applicationId) {
  return request({
    url: buildApplicationPath(applicationId),
    method: 'get',
    acceptNotFound: true,
    validateStatus: (status) => (status >= 200 && status < 300) || status === 404,
  })
}

export function createApplication(data) {
  return request({ url: '/ai/applications', method: 'post', data })
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

export function listDeveloperKeys(applicationId) {
  return request({ url: buildApplicationPath(applicationId, 'keys'), method: 'get' })
}

export function createDeveloperKey(applicationId, data) {
  return request({ url: buildApplicationPath(applicationId, 'keys'), method: 'post', data, headers: { repeatSubmit: false } })
}

export function rotateDeveloperKey(applicationId, keyId, data) {
  return request({ url: buildApplicationPath(applicationId, `keys/${keyId}/rotate`), method: 'post', data, headers: { repeatSubmit: false } })
}

export function revokeDeveloperKey(applicationId, keyId) {
  return request({ url: buildApplicationPath(applicationId, `keys/${keyId}/revoke`), method: 'post' })
}

export function listApplicationUsage(applicationId, query) {
  return request({
    url: buildApplicationPath(applicationId, 'usage'),
    method: 'get',
    params: query,
  })
}
