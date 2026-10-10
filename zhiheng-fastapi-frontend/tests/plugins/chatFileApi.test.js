import assert from 'node:assert/strict'
import { loadChatApis } from './helpers/loadChatApis.js'

const requests = []
let response = { code: 200, fileId: 'private-file', originalFilename: 'report.pdf' }
let rejectBusiness = false
let networkFailure = false
const { chat: scope } = await loadChatApis(async config => {
  requests.push(config)
  if (networkFailure) throw new Error('网络连接已断开')
  for (const transform of config.transformResponse || []) transform(JSON.stringify(response))
  if (rejectBusiness) throw 'error'
  return response
})
assert.equal(typeof scope.uploadChatFile, 'function', 'The chat API must expose a protected shared-client file upload')
const upload = new File(['sample'], 'report.pdf', { type: 'application/pdf' })
const controller = new AbortController()
const onUploadProgress = () => {}
await scope.uploadChatFile(upload, { signal: controller.signal, onUploadProgress })
assert.equal(requests[0].url, '/common/files/upload')
assert.equal(requests[0].data.get('file'), upload)
assert.equal(requests[0].headers.repeatSubmit, false, 'Each FormData is a distinct upload even within one second')
assert.equal(requests[0].headers['Content-Type'], 'multipart/form-data')
assert.equal(requests[0].signal, controller.signal)
assert.equal(requests[0].onUploadProgress, onUploadProgress)
assert.ok(requests[0].timeout >= 120000)
response = { code: 400, msg: '仅支持本人有效文件' }
rejectBusiness = true
await assert.rejects(scope.uploadChatFile(upload), /仅支持本人有效文件/)
rejectBusiness = false
response = { code: 200 }
await assert.rejects(scope.uploadChatFile(upload), /有效的文件信息/)
networkFailure = true
await assert.rejects(scope.uploadChatFile(upload), /网络连接已断开/)
networkFailure = false
response = new Blob(['example'], { type: 'text/plain' })
await scope.downloadChatFile({ fileId: 'private-id', fileName: '中文.txt', downloadUrl: 'https://untrusted.invalid/file' }, controller.signal)
const download = requests.at(-1)
assert.equal(download.url, '/common/files/private-id/download/%E4%B8%AD%E6%96%87.txt')
assert.equal(download.responseType, 'blob')
assert.equal(download.signal, controller.signal)
