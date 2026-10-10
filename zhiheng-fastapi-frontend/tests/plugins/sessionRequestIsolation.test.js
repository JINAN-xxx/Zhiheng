import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import axios from 'axios'
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { sessionLifecyclePlugin } from '../../src/store/sessionLifecycle.js'
import { normalizeErrorMessage } from '../../src/utils/errorMessage.js'
import { loadWorkStores } from './helpers/loadWorkStores.js'

const source = readFileSync(new URL('../../src/utils/request.js', import.meta.url), 'utf8')
const expiration = source.slice(source.indexOf('function handleExpiredSession('), source.indexOf('axios.defaults.headers'))
const responses = source.slice(source.indexOf('service.interceptors.response.use('), source.indexOf('/**\n * 通用文件下载方法'))

for (const kind of ['http', 'business']) {
  let token = 'old-token'
  const modules = await loadWorkStores({ auth: {
    getToken: () => token,
    setToken: value => { token = value },
    removeToken: () => { token = '' },
  } })
  const pinia = createPinia().use(sessionLifecyclePlugin)
  createApp({}).use(pinia)
  const user = modules.user.default(pinia)
  const work = modules.work.useWorkTaskStore(pinia)
  const service = axios.create()
  const redirects = []
  const scope = {
    axios, service, normalizeErrorMessage,
    useUserStore: () => user,
    getToken: () => token,
    isRelogin: { show: false },
    location: { pathname: '/ai/chat/work', search: '', hash: '', replace: path => redirects.push(path) },
    decryptTransportErrorResponse: async error => error,
    decryptTransportResponse: async response => response,
    shouldRetryTransportWithFreshKey: () => false,
    console: { log() {} },
    feedback: { msgError() {}, msgWarning() {}, notifyError() {} },
    errorCode: {},
  }
  vm.runInNewContext(expiration + '\n' + responses, scope)

  function expiredResponse(config, resolve, reject) {
    if (kind === 'http') {
      const response = { status: 401, data: {}, config }
      reject(new axios.AxiosError('unauthorized', 'ERR_BAD_REQUEST', config, {}, response))
    } else {
      resolve({ status: 200, data: { code: 401 }, config, request: { responseType: '' } })
    }
  }
  let release
  let started
  const requestStarted = new Promise(resolve => { started = resolve })
  const oldRequest = service.get('/agent_extension/work-tasks', {
    headers: { Authorization: `Bearer ${token}` },
    adapter: config => new Promise((resolve, reject) => {
      release = () => expiredResponse(config, resolve, reject)
      started()
    }),
  })
  await requestStarted
  await user.login({ username: 'next-user', password: 'password' })
  work.tasks = [{ taskId: 'new-account-task' }]
  release()
  await assert.rejects(oldRequest, /登录状态已失效/)
  assert.equal(token, 'new-token', `${kind}: a stale 401 must not clear the new authentication`)
  assert.equal(work.tasks[0].taskId, 'new-account-task', `${kind}: a stale 401 must not clear new Work state`)
  assert.deepEqual(redirects, [])

  const anonymous = service.post('/login', {}, {
    headers: { isToken: false },
    adapter: config => new Promise((resolve, reject) => expiredResponse(config, resolve, reject)),
  })
  await assert.rejects(anonymous, /登录状态已失效/)
  assert.equal(token, 'new-token', `${kind}: an unauthenticated request must not expire an existing session`)
  assert.equal(work.tasks.length, 1)

  const currentRequest = service.get('/agent_extension/work-tasks', {
    headers: { Authorization: `Bearer ${token}` },
    adapter: config => new Promise((resolve, reject) => expiredResponse(config, resolve, reject)),
  })
  await assert.rejects(currentRequest, /登录状态已失效/)
  assert.equal(token, '', `${kind}: the current identity's 401 still clears authentication`)
  assert.equal(work.tasks.length, 0)
  assert.deepEqual(redirects, ['/login?redirect=%2Fai%2Fchat%2Fwork'])
}
console.log('ok stale HTTP/business 401 cannot expire a new session; current identity 401 still clears Work')
