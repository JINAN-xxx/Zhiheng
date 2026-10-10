import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import axios from 'axios'
import { normalizeErrorMessage } from '../../src/utils/errorMessage.js'

const requestSource = readFileSync(new URL('../../src/utils/request.js', import.meta.url), 'utf8')
const responseInterceptor = requestSource.slice(
  requestSource.indexOf('service.interceptors.response.use('),
  requestSource.indexOf('/**\n * 通用文件下载方法'),
)
const menuSource = readFileSync(new URL('../../src/api/menu.js', import.meta.url), 'utf8')
  .replace(/^import .*\n/m, '')
  .replace('export const getRouters =', 'globalThis.getRouters =')
const permissionSource = readFileSync(new URL('../../src/store/modules/permission.js', import.meta.url), 'utf8')
const generateRoutes = permissionSource.match(/(?:async )?generateRoutes\(roles\) \{[\s\S]*?\n      \}/)[0]

async function outcomeWithinDeadline(promise) {
  let timer
  try {
    return await Promise.race([
      promise.then(value => ({ status: 'resolved', value }), error => ({ status: 'rejected', error })),
      new Promise(resolve => { timer = setTimeout(() => resolve({ status: 'pending' }), 100) }),
    ])
  } finally {
    clearTimeout(timer)
  }
}

function loadRoutes(failure) {
  let failing = true
  const registered = []
  const updates = []
  const notice = []
  const constant = [{ path: '/login' }]
  const menu = [{ path: '/system/user' }]
  const workspace = { path: '/ai/chat', meta: { workspaceAccess: true } }
  const detail = { path: '/system/user/detail', meta: {} }
  const service = axios.create({ adapter: async config => {
    if (failing && failure === 'http') {
      throw new axios.AxiosError('menu unavailable', 'ERR_BAD_RESPONSE', config, {}, {
        status: 500, data: { msg: 'menu unavailable' }, config,
      })
    }
    if (failing && failure === 'timeout') {
      throw new axios.AxiosError('timeout of 10000ms exceeded', 'ECONNABORTED', config)
    }
    return { status: 200, data: { code: 200, data: menu }, config, request: { responseType: '' } }
  } })
  const context = {
    axios, service, request: service, normalizeErrorMessage,
    decryptTransportErrorResponse: async error => error,
    decryptTransportResponse: async response => response,
    shouldRetryTransportWithFreshKey: () => false,
    feedback: { msgError: message => notice.push(message), msgWarning() {}, notifyError() {} },
    console: { log() {} }, errorCode: {},
    handleExpiredSession() { assert.fail('A menu failure must not enter expired-session handling') },
    filterRetiredWorkspaceRoutes: routes => {
      if (failing && failure === 'conversion') throw new Error('route conversion failed')
      return routes
    },
    filterAsyncRouter: routes => routes,
    filterDynamicRoutes: routes => routes,
    dynamicRoutes: [workspace, detail],
    constantRoutes: constant,
    router: { addRoute: route => registered.push(route) },
  }
  vm.runInNewContext(responseInterceptor + '\n' + menuSource + '\nglobalThis.actions = { ' + generateRoutes + ' };', context)
  const store = Object.assign(context.actions, Object.fromEntries(
    ['setRoutes', 'setSidebarRouters', 'setDefaultRoutes', 'setTopbarRoutes']
      .map(name => [name, routes => updates.push([name, JSON.parse(JSON.stringify(routes))])]),
  ))
  return { store, registered, updates, notice, retry: () => { failing = false } }
}

const failures = []
for (const failure of ['http', 'timeout', 'conversion']) {
  const context = loadRoutes(failure)
  const unhandled = []
  const capture = error => unhandled.push(error)
  process.on('unhandledRejection', capture)
  try {
    const result = await outcomeWithinDeadline(context.store.generateRoutes())
    assert.equal(result.status, 'rejected', `${failure}: route generation must settle with the original failure`)
    assert.match(result.error.message, /menu unavailable|timeout|route conversion failed/)
    assert.deepEqual(unhandled, [], `${failure}: every rejection must reach the awaiting navigation guard`)
    assert.deepEqual(context.registered, [])
    assert.deepEqual(context.updates, [])

    context.retry()
    const retried = await outcomeWithinDeadline(context.store.generateRoutes())
    assert.equal(retried.status, 'resolved')
    assert.deepEqual(JSON.parse(JSON.stringify(retried.value)), [
      { path: '/system/user' }, { path: '/ai/chat', meta: { workspaceAccess: true } },
    ])
    assert.equal(context.registered.length, 1)
    assert.equal(context.registered[0].path, '/system/user/detail')
    assert.deepEqual(context.updates.map(([name]) => name), [
      'setRoutes', 'setSidebarRouters', 'setDefaultRoutes', 'setTopbarRoutes',
    ])
  } catch (error) {
    failures.push(error)
  } finally {
    process.off('unhandledRejection', capture)
  }
}

// Router guards can recover by redirecting to /login: router.push then resolves,
// so loading must also end when navigation did not throw back to handleLogin.
const loginSource = readFileSync(new URL('../../src/views/login.vue', import.meta.url), 'utf8')
const handleLogin = loginSource.match(/async function handleLogin\(\) \{[\s\S]*?(?=\nasync function getCode)/)[0]
const login = {
  loading: { value: false },
  loginForm: { value: { username: 'test-user', password: 'test-only', rememberMe: false } },
  userStore: { login: async () => {} },
  route: { query: {} }, redirect: { value: undefined }, defaultLandingPath: '/ai/chat',
  router: { push: async () => undefined },
  Cookies: { remove() {}, set() {} }, captchaEnabled: { value: false },
  getCode: async () => {},
}
vm.runInNewContext(handleLogin + '\nglobalThis.submit = handleLogin;', login)
try {
  await login.submit()
  assert.equal(login.loading.value, false, 'A guard redirect back to login must unlock the submit button')
} catch (error) {
  failures.push(error)
}
assert.deepEqual(failures.map(error => error.message), [])
console.log('ok menu HTTP/timeout/conversion failures reach navigation recovery, retry succeeds, login loading ends')
