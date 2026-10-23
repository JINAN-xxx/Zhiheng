import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import axios from 'axios'
import { normalizeErrorMessage } from '../../src/utils/errorMessage.js'

// Install the real shared response interceptor on a real Axios instance, without browser globals.
const source = readFileSync(new URL('../../src/utils/request.js', import.meta.url), 'utf8')
const interceptor = source.slice(source.indexOf('service.interceptors.response.use('), source.indexOf('/**\n * 通用文件下载方法'))
function harness() {
  const service = axios.create()
  const calls = { decrypt: 0, retry: 0, logout: 0, feedback: [], logs: [] }
  const scope = {
    axios, service, normalizeErrorMessage,
    decryptTransportErrorResponse: async error => { calls.decrypt++; return error },
    decryptTransportResponse: async response => response,
    shouldRetryTransportWithFreshKey: () => { calls.retry++; return false },
    handleExpiredSession: () => { calls.logout++ },
    console: { log: (...args) => calls.logs.push(args) },
    feedback: {
      msgError: message => calls.feedback.push(['error', message]),
      msgWarning: message => calls.feedback.push(['warning', message]),
      notifyError: message => calls.feedback.push(['notification', message]),
    },
    errorCode: {},
  }
  vm.runInNewContext(interceptor, scope)
  return { service, calls }
}

{
  const { service, calls } = harness()
  const controller = new AbortController()
  let adapterStarted, cancelled
  const started = new Promise(resolve => { adapterStarted = resolve })
  const request = service.get('/common/files/upload', {
    signal: controller.signal,
    adapter: config => new Promise((resolve, reject) => {
      config.signal.addEventListener('abort', () => {
        cancelled = new axios.CanceledError('canceled', config)
        reject(cancelled)
      }, { once: true })
      adapterStarted()
    }),
  })
  await started
  controller.abort()
  await assert.rejects(request, error => error === cancelled && axios.isCancel(error))
  assert.equal(calls.decrypt, 0, 'An intentional abort has no server response to decrypt')
  assert.equal(calls.retry, 0, 'A cancelled upload must never initiate crypto recovery')
  assert.deepEqual(calls.feedback, [], 'Navigating away from pending uploads must not display cancelled error toasts')
  assert.deepEqual(calls.logs, [], 'Expected cancellation does not need an error log')
}

// Preserve the established treatment of actual network, server, and expired-session failures.
{
  const { service, calls } = harness()
  const networkError = new Error('Network Error')
  await assert.rejects(service.get('/example', { adapter: async () => { throw networkError } }), error => error === networkError)
  assert.equal(calls.decrypt, 1)
  assert.deepEqual(calls.feedback, [['error', '后端接口连接异常']])
}
{
  const { service, calls } = harness()
  const error = Object.assign(new Error('server failure'), { response: { status: 400, data: { detail: '文件已过期' } } })
  await assert.rejects(service.get('/example', { adapter: async () => { throw error } }), /文件已过期/)
  assert.equal(calls.decrypt, 1)
  assert.deepEqual(calls.feedback, [['error', '文件已过期']])
}
{
  const { service, calls } = harness()
  const error = Object.assign(new Error('unauthorized'), { response: { status: 401 } })
  await assert.rejects(service.get('/example', { adapter: async () => { throw error } }), /登录状态已失效/)
  assert.equal(calls.logout, 1)
  assert.equal(calls.decrypt, 1)
}
