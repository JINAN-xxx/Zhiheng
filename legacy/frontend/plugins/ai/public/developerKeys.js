import request from '@/utils/request'
import { buildApplicationPath } from '../utils/application.js'

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

export { developerKeyStatusLabel, extractIssuedKey, normalizeDeveloperKeys } from '../utils/application.js'
export { buildPublicApiBase, copyText } from '../utils/apiDocs.js'
