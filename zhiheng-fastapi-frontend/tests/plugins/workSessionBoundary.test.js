import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { createApp, computed } from 'vue'
import { createPinia, defineStore } from 'pinia'
import { loadWorkStores } from './helpers/loadWorkStores.js'

const publicPath = new URL('../../plugins/agent_extension/public/workTasks.js', import.meta.url)
assert.ok(existsSync(publicPath), 'Work must expose its own public entry')
const { sessionLifecyclePlugin, clearSessionState } = await import('../../src/store/sessionLifecycle.js')
const createScope = () => {
  const pinia = createPinia().use(sessionLifecyclePlugin)
  createApp({}).use(pinia)
  return pinia
}
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
const task = taskId => ({ taskId, status: 'active', goal: 'private', members: [], messages: [] })

const calls = []
const modules = await loadWorkStores({ api: {
  listWorkTasks: async query => {
    calls.push(['list', query])
    return { rows: [task('task-a')], total: 1, pageNum: 2, pageSize: 10 }
  },
  updateWorkTask: async (id, changes) => {
    calls.push(['rename', id, changes])
    return { data: { ...task(id), title: changes.title } }
  },
  deleteWorkTask: async id => { calls.push(['remove', id]) },
} })
assert.equal(modules.legacy.default, modules.work.useWorkTaskStore)
const scope = createScope()
const store = modules.work.useWorkTaskStore(scope)
assert.equal(modules.legacy.default(scope), store)
const navigation = modules.work.useWorkTaskNavigation(scope)
const visibleCount = computed(() => navigation.tasks.length)
assert.equal(visibleCount.value, 0)
await navigation.loadTasks({ keyword: 'selected', pageNum: 2, pageSize: 10 })
assert.equal(visibleCount.value, 1, 'Navigation must remain reactive after the list changes')
assert.equal(navigation.total, 1)
assert.equal(navigation.pageNum, 2)
assert.equal(navigation.pageSize, 10)
assert.equal(navigation.listQuery.keyword, 'selected')
assert.equal(navigation.runMessage, undefined, 'The sidebar only gets navigation capabilities')
assert.equal(navigation.createDraft, undefined)
await navigation.renameListedTask({ taskId: 'task-a', version: 3 }, 'Renamed')
assert.equal(navigation.tasks[0].title, 'Renamed')
await navigation.removeTask('task-a')
assert.equal(visibleCount.value, 0)
assert.deepEqual(calls[1], ['rename', 'task-a', { expectedVersion: 3, title: 'Renamed' }])
assert.deepEqual(calls[2], ['remove', 'task-a'])
assert.equal(modules.work.resolveWorkTaskCapabilities(['agent_extension:work:list']).list, true)

// Exercise the production user actions, including the same clearSession path used by 401 recovery.
for (const action of ['clearSession', 'logOut', 'login']) {
  const lateList = deferred()
  const lateDetail = deferred()
  const lateCreate = deferred()
  const lateAgents = deferred()
  let token = 'old-token'
  const authCalls = []
  let activeWork
  const loaded = await loadWorkStores({
    api: {
      listWorkTasks: () => lateList.promise,
      getWorkTask: () => lateDetail.promise,
      createWorkTask: () => lateCreate.promise,
      listAvailableWorkAgents: () => lateAgents.promise,
    },
    auth: {
      getToken: () => token,
      removeToken: () => { token = ''; authCalls.push('removed') },
      setToken: value => { token = value; authCalls.push('set') },
      logout: async previous => {
        assert.equal(previous, 'old-token')
        assert.equal(token, '')
        assert.equal(activeWork.currentTask, null)
        authCalls.push('logout')
        throw new Error('backend unavailable')
      },
    },
  })
  const pinia = createScope()
  const user = loaded.user.default(pinia)
  const work = loaded.work.useWorkTaskStore(pinia)
  activeWork = work
  work.currentTask = task('private-task')
  work.tasks = [task('private-task')]
  work.createDraft.goal = 'private draft'
  const pending = [
    work.loadTasks(), work.loadTask('old-task'), work.loadAvailableAgents(),
    work.createTask({ goal: 'old goal', agentIds: ['agent-a'], activeAgentId: 'agent-a' }),
  ]
  const streaming = new AbortController()
  const observing = new AbortController()
  work.streamController = streaming
  work.observeController = observing
  work.runState = 'streaming'
  if (action === 'login') await user.login({ username: 'next-user', password: 'password' })
  else await user[action]()
  assert.equal(streaming.signal.aborted, true, action)
  assert.equal(observing.signal.aborted, true, action)
  assert.equal(work.currentTask, null, action)
  assert.equal(work.createDraft.goal, '', action)
  assert.equal(work.listLoading, false, action)
  assert.equal(work.detailLoading, false, action)
  assert.equal(token, action === 'login' ? 'new-token' : '', action)
  assert.deepEqual(authCalls, action === 'login' ? ['removed', 'set'] : action === 'logOut' ? ['removed', 'logout'] : ['removed'])
  // Simulate another cleanup and a new account's state before old requests complete.
  clearSessionState(user)
  work.currentTask = task('new-account-task')
  work.tasks = [task('new-account-task')]
  lateList.resolve({ rows: [task('old-task')], total: 1 })
  lateDetail.resolve({ data: task('old-task') })
  lateCreate.resolve({ data: task('old-created-task') })
  lateAgents.resolve({ data: [{ agentId: 'old-agent' }] })
  await Promise.allSettled(pending)
  assert.equal(work.currentTask.taskId, 'new-account-task', action)
  assert.equal(work.tasks[0].taskId, 'new-account-task', action)
  assert.equal(work.availableAgents.length, 0, action)
  assert.equal(work.runState, 'idle', action)
}

const loginError = new Error('login rejected')
const failedLogin = await loadWorkStores({ auth: { login: async () => { throw loginError } } })
const failedScope = createScope()
const existingWork = failedLogin.work.useWorkTaskStore(failedScope)
existingWork.currentTask = task('existing-task')
await assert.rejects(failedLogin.user.default(failedScope).login({ username: 'user', password: 'bad' }), e => e === loginError)
assert.equal(existingWork.currentTask.taskId, 'existing-task', 'Failed login must not clear the current session')

const streamReady = deferred()
const continueStream = deferred()
let streamSignal
let staleDetailReads = 0
const streamingModules = await loadWorkStores({ api: {
  createWorkTaskRunStream: async (id, payload, signal) => {
    streamSignal = signal
    return (async function* () {
      yield { event: 'meta', data: { taskId: id, runId: 'old-run', messageId: 'reply-a' } }
      streamReady.resolve()
      await continueStream.promise
      yield { event: 'content', data: { taskId: id, messageId: 'reply-a', delta: 'old account content' } }
      yield { event: 'done', data: { taskId: id, status: 'completed' } }
    })()
  },
  getWorkTask: async () => { staleDetailReads += 1; return { data: task('old-task') } },
} })
const streamingScope = createScope()
const streamingUser = streamingModules.user.default(streamingScope)
const streamingWork = streamingModules.work.useWorkTaskStore(streamingScope)
streamingWork.currentTask = task('old-task')
const pendingRun = streamingWork.runMessage({ agentId: 'agent-a', message: 'old goal', clientMessageId: 'old-request' })
await streamReady.promise
streamingUser.clearSession()
assert.equal(streamSignal.aborted, true)
streamingWork.currentTask = task('new-task')
continueStream.resolve()
assert.equal(await pendingRun, false)
assert.equal(streamingWork.currentTask.taskId, 'new-task')
assert.deepEqual(streamingWork.currentTask.messages, [])
assert.equal(streamingWork.runState, 'idle')
assert.equal(staleDetailReads, 0, 'The old stream must not start polling after cleanup')

const pollResponse = deferred()
let pollReads = 0
const observingModules = await loadWorkStores({ api: {
  getWorkTask: () => { pollReads += 1; return pollResponse.promise },
} })
const observingScope = createScope()
const observingUser = observingModules.user.default(observingScope)
const observingWork = observingModules.work.useWorkTaskStore(observingScope)
observingWork.currentTask = { ...task('old-task'), currentRunId: 'old-run' }
const pendingObservation = observingWork.observeUntilReleased('old-task')
const pollController = observingWork.observeController
observingUser.clearSession()
assert.equal(pollController.signal.aborted, true)
observingWork.currentTask = task('new-task')
pollResponse.resolve({ data: { ...task('old-task'), currentRunId: 'old-run' } })
assert.equal(await pendingObservation, false)
assert.equal(pollReads, 1, 'Cleanup must prevent scheduling another poll')
assert.equal(observingWork.currentTask.taskId, 'new-task')

const brokenScope = createScope()
const brokenUser = modules.user.default(brokenScope)
defineStore('broken-module', {
  sessionCleanup: 'clearPrivateState',
  actions: { clearPrivateState() { throw new Error('private error') } },
})(brokenScope)
const healthyWork = modules.work.useWorkTaskStore(brokenScope)
healthyWork.currentTask = task('private-task')
const warnings = []
const originalWarn = console.warn
try {
  console.warn = message => warnings.push(message)
  brokenUser.clearSession()
} finally {
  console.warn = originalWarn
}
assert.equal(brokenUser.token, '')
assert.equal(healthyWork.currentTask, null)
assert.deepEqual(warnings, ['部分模块的会话状态未能清理。'])

const mutationError = new Error('forbidden')
const rejectedModules = await loadWorkStores({ api: {
  deleteWorkTask: async () => { throw mutationError },
} })
await assert.rejects(rejectedModules.work.useWorkTaskNavigation(createScope()).removeTask('task-a'), e => e === mutationError)

// Static guard for the pilot's boundary; behaviour is exercised above with real modules.
const srcRoot = new URL('../../src/', import.meta.url)
for (const relative of readdirSync(srcRoot, { recursive: true })) {
  if (!/\.(js|vue)$/.test(relative)) continue
  const source = readFileSync(new URL(relative, srcRoot), 'utf8')
  for (const match of source.matchAll(/\bfrom\s+['"]([^'"]+)['"]/g)) {
    assert.doesNotMatch(match[1], /plugins\/agent_extension\/(?!public\/)/, `${relative} imports Work internals`)
  }
}
console.log('ok Work public boundary, sidebar actions, logout/401/account changes and late-response isolation')
