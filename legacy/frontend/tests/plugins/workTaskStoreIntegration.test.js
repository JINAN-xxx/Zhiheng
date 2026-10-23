import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { createPinia, defineStore, setActivePinia } from 'pinia'
import { v4 as uuidv4 } from 'uuid'
import * as workUtils from '../../plugins/agent_extension/utils/workTask.js'
import { createApp } from 'vue'
import { sessionLifecyclePlugin, clearSessionState } from '../../src/store/sessionLifecycle.js'

// Execute the production actions with real Pinia state/getters and replace only
// network imports. This catches wiring errors outside the pure transforms.
const source = readFileSync(new URL('../../plugins/agent_extension/stores/workTask.js', import.meta.url), 'utf8')
  .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\s*$/gm, '')
  .replace('export default useWorkTaskStore', 'globalThis.useStore = useWorkTaskStore')

const agentId = '11111111-1111-4111-8111-111111111111'
const member = {
  agentId, agentNameSnapshot: '研究助手', publishedVersion: 2,
  latestPublishedVersion: 3, available: true, removedAt: null,
}
const task = (taskId, overrides = {}) => ({
  taskId, title: '工作', goal: '整理资料', status: 'active', version: 1,
  activeAgentId: agentId, currentRunId: null, members: [{ ...member }], messages: [],
  ...overrides,
})
const reply = (value) => ({ code: 200, data: value })
const deferred = () => {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function actualStore(api) {
  const pinia = createPinia().use(sessionLifecyclePlugin)
  createApp({}).use(pinia)
  setActivePinia(pinia)
  const scope = {
    ...workUtils,
    defineStore,
    uuidv4,
    crypto: globalThis.crypto,
    AbortController,
    DOMException,
    setTimeout,
    clearTimeout,
    listWorkTasks: async () => ({ code: 200, rows: [], total: 0, pageNum: 1, pageSize: 20 }),
    ...api,
  }
  vm.runInNewContext(source, scope, { filename: 'workTask.production-store.js' })
  return scope.useStore()
}

const failures = []
async function check(name, body) {
  try {
    await body()
    console.log('ok work store: ' + name)
  } catch (error) {
    failures.push({ name, error })
    console.error('not ok work store: ' + name + ': ' + error.message)
  }
}

await check('version update confirms the currently published target version', async () => {
  let submitted
  const store = actualStore({
    updateWorkTaskMemberVersion: async (taskId, selectedAgentId, payload) => {
      submitted = { taskId, selectedAgentId, payload }
      return reply(task('task-a', { version: 2 }))
    },
  })
  store.currentTask = task('task-a')
  await store.upgradeMember(member)
  assert.equal(submitted.taskId, 'task-a')
  assert.equal(submitted.selectedAgentId, agentId)
  assert.equal(submitted.payload.expectedPublishedVersion, 3)
})

await check('a stream failure refreshes the persisted run before unlocking', async () => {
  let detailReads = 0
  const store = actualStore({
    createWorkTaskRunStream: async () => (async function* () {
      yield { event: 'meta', data: {
        taskId: 'task-a', runId: 'run-a', messageId: 'message-a',
        agentId, agentName: member.agentNameSnapshot, publishedVersion: 2,
      } }
      throw new Error('connection dropped')
    })(),
    getWorkTask: async () => {
      detailReads += 1
      return reply(task('task-a', { messages: [{
        messageId: 'message-a', taskId: 'task-a', role: 'assistant',
        runId: 'run-a', runStatus: 'cancelled', content: '保留的部分结果',
      }] }))
    },
  })
  store.currentTask = task('task-a')
  try {
    await store.runMessage({ agentId, message: '整理资料', clientMessageId: 'request-a' })
  } catch (error) {
    assert.equal(error.message, 'connection dropped')
  }
  assert.ok(detailReads > 0, 'the actual failure path must read the persisted task')
  assert.equal(store.currentTask.currentRunId, null)
  assert.equal(store.runLocked, false)
})

await check('a late cancel response cannot change the next task', async () => {
  const cancellation = deferred()
  let detailReads = 0
  const store = actualStore({
    cancelWorkTaskRun: () => cancellation.promise,
    getWorkTask: async () => {
      detailReads += 1
      return reply(task('task-a'))
    },
  })
  store.currentTask = task('task-a', { currentRunId: 'run-a' })
  store.runId = 'run-a'
  store.runState = 'streaming'
  const pending = store.cancelRun()
  store.clearCurrentTask()
  store.currentTask = task('task-b')
  cancellation.resolve({ code: 200 })
  await pending
  assert.equal(store.currentTask.taskId, 'task-b')
  assert.equal(store.runState, 'idle')
  assert.equal(store.runLocked, false)
  assert.equal(detailReads, 0)
})

await check('logout discards late task creation and agent catalogue responses', async () => {
  const creation = deferred()
  const catalogue = deferred()
  const store = actualStore({
    createWorkTask: () => creation.promise,
    listAvailableWorkAgents: () => catalogue.promise,
  })
  const pendingCreation = store.createTask({ goal: '私有工作目标', agentIds: [agentId], activeAgentId: agentId })
  const pendingCatalogue = store.loadAvailableAgents()
  clearSessionState(store)
  creation.resolve(reply(task('previous-user-task')))
  catalogue.resolve(reply([{ agentId, name: '前一个账号的智能体' }]))
  await Promise.allSettled([pendingCreation, pendingCatalogue])
  assert.equal(store.currentTask, null)
  assert.equal(store.createAttempt, null)
  assert.equal(store.createDraft.goal, '')
  assert.equal(store.availableAgents.length, 0)
})

await check('a released claim with a pending persisted reply keeps polling to a terminal reply', async () => {
  let detailReads = 0
  const user = {
    messageId: 'user-a', taskId: 'task-a', role: 'user',
    clientMessageId: 'request-a', content: '整理资料',
  }
  const store = actualStore({
    getWorkTask: async () => {
      detailReads += 1
      const runStatus = detailReads === 1 ? 'pending' : 'completed'
      return reply(task('task-a', { messages: [user, {
        messageId: 'reply-a', taskId: 'task-a', role: 'assistant',
        replyToMessageId: user.messageId, runId: 'run-a', runStatus, content: '结果',
      }] }))
    },
  })
  store.currentTask = task('task-a')
  store.retryAttempt = { agentId, message: user.content, clientMessageId: user.clientMessageId, kind: 'uncertain' }
  store.runState = 'observing'
  await store.observeUntilReleased('task-a', store.runGeneration)
  assert.equal(detailReads, 2)
  assert.equal(store.runState, 'completed')
  assert.equal(store.runLocked, false)
})

await check('run meta immediately marks the matching task summary as claimed', async () => {
  const store = actualStore({})
  store.currentTask = task('task-a')
  store.tasks = [task('task-a')]
  store.workflow.currentTask = store.currentTask
  const generation = store.workflow.beginRun()
  store.runGeneration = generation
  store.applyRunEvent({ event: 'meta', data: {
    taskId: 'task-a', runId: 'run-a', messageId: 'reply-a',
    agentId, agentName: member.agentNameSnapshot, publishedVersion: 2,
  } }, generation)
  assert.equal(store.tasks[0].currentRunId, 'run-a')
})

await check('list refreshes retain the active keyword and archived filter', async () => {
  const queries = []
  const store = actualStore({
    listWorkTasks: async (query) => {
      queries.push({ ...query })
      return { code: 200, rows: [], total: 0, pageNum: query.pageNum, pageSize: query.pageSize }
    },
  })
  await store.loadTasks({ keyword: 'specific', status: 'archived', pageNum: 2, pageSize: 10 })
  await store.loadTasks()
  assert.deepEqual(queries[1], queries[0])
})

await check('leaving work invalidates a late task creation response', async () => {
  const creation = deferred()
  const store = actualStore({ createWorkTask: () => creation.promise })
  const pending = store.createTask({ goal: '稍后完成', agentIds: [agentId], activeAgentId: agentId })
  store.stopLocalActivity()
  creation.resolve(reply(task('late-task')))
  const result = await pending
  assert.equal(result, false)
  assert.equal(store.currentTask, null)
  assert.equal(store.createLoading, false)
})

await check('a confirmed failed run keeps its draft and retries with a new client key', async () => {
  const attempts = []
  const store = actualStore({
    createWorkTaskRunStream: async (taskId, payload) => {
      attempts.push({ ...payload })
      const failed = attempts.length === 1
      return (async function* () {
        yield { event: 'meta', data: {
          taskId, runId: failed ? 'run-failed' : 'run-completed',
          messageId: failed ? 'reply-failed' : 'reply-completed',
          agentId, agentName: member.agentNameSnapshot, publishedVersion: 2,
        } }
        if (failed) yield { event: 'error', data: {
          taskId, runId: 'run-failed', messageId: 'reply-failed', message: 'upstream failed',
        } }
        yield { event: 'done', data: {
          taskId, runId: failed ? 'run-failed' : 'run-completed',
          messageId: failed ? 'reply-failed' : 'reply-completed',
          status: failed ? 'failed' : 'completed',
        } }
      })()
    },
    getWorkTask: async () => {
      const attempt = attempts.at(-1)
      const failed = attempts.length === 1
      const user = {
        messageId: failed ? 'user-failed' : 'user-completed', taskId: 'task-a', role: 'user',
        clientMessageId: attempt.clientMessageId, content: attempt.message,
      }
      return reply(task('task-a', { messages: [user, {
        messageId: failed ? 'reply-failed' : 'reply-completed', taskId: 'task-a', role: 'assistant',
        replyToMessageId: user.messageId, runId: failed ? 'run-failed' : 'run-completed',
        runStatus: failed ? 'failed' : 'completed', content: failed ? '' : '完成',
      }] }))
    },
  })
  store.currentTask = task('task-a')
  const firstCompleted = await store.runMessage({ agentId, message: '保留这条草稿', clientMessageId: 'failed-key' })
  assert.equal(firstCompleted, false)
  assert.equal(store.draftMessage, '保留这条草稿')
  assert.equal(store.retryAttempt.kind, 'failed')
  const retryCompleted = await store.retryRun({ confirmedFailed: store.retryAttempt.kind === 'failed' })
  assert.equal(retryCompleted, true)
  assert.notEqual(attempts[1].clientMessageId, attempts[0].clientMessageId)
})

await check('a dropped stream adopts the persisted failed status before choosing its retry key', async () => {
  const attempts = []
  const store = actualStore({
    createWorkTaskRunStream: async (taskId, payload) => {
      attempts.push({ ...payload })
      const failed = attempts.length === 1
      return (async function* () {
        yield { event: 'meta', data: {
          taskId, runId: failed ? 'run-dropped' : 'run-retry',
          messageId: failed ? 'reply-dropped' : 'reply-retry',
          agentId, agentName: member.agentNameSnapshot, publishedVersion: 2,
        } }
        if (failed) throw new Error('network dropped')
        yield { event: 'done', data: {
          taskId, runId: 'run-retry', messageId: 'reply-retry', status: 'completed',
        } }
      })()
    },
    getWorkTask: async () => {
      const attempt = attempts.at(-1)
      const failed = attempts.length === 1
      const user = {
        messageId: failed ? 'user-dropped' : 'user-retry', taskId: 'task-a', role: 'user',
        clientMessageId: attempt.clientMessageId, content: attempt.message,
      }
      return reply(task('task-a', { messages: [user, {
        messageId: failed ? 'reply-dropped' : 'reply-retry', taskId: 'task-a', role: 'assistant',
        replyToMessageId: user.messageId, runId: failed ? 'run-dropped' : 'run-retry',
        runStatus: failed ? 'failed' : 'completed', content: failed ? '' : '完成',
      }] }))
    },
  })
  store.currentTask = task('task-a')
  await assert.rejects(
    store.runMessage({ agentId, message: '断线任务', clientMessageId: 'dropped-key' }),
    /network dropped/,
  )
  assert.equal(store.retryAttempt.kind, 'failed')
  await store.retryRun({ confirmedFailed: store.retryAttempt.kind === 'failed' })
  assert.notEqual(attempts[1].clientMessageId, attempts[0].clientMessageId)
})

await check('loading a persisted pending reply observes it through its terminal status', async () => {
  let detailReads = 0
  const user = {
    messageId: 'user-persisted', taskId: 'task-a', role: 'user',
    clientMessageId: 'persisted-key', content: '恢复任务',
  }
  const store = actualStore({
    getWorkTask: async () => {
      detailReads += 1
      const runStatus = detailReads === 1 ? 'pending' : 'completed'
      return reply(task('task-a', { messages: [user, {
        messageId: 'reply-persisted', taskId: 'task-a', role: 'assistant',
        replyToMessageId: user.messageId, runId: 'run-persisted', runStatus, content: '结果',
      }] }))
    },
  })
  await store.loadTask('task-a')
  await new Promise((resolve) => setTimeout(resolve, 20))
  assert.equal(detailReads, 2)
  assert.equal(store.runState, 'completed')
  assert.equal(store.runLocked, false)
})

await check('an old completed reply cannot confirm a newly rejected request', async () => {
  const oldUser = {
    messageId: 'old-user', taskId: 'task-a', role: 'user',
    clientMessageId: 'old-key', content: '旧消息',
  }
  const persisted = task('task-a', { messages: [oldUser, {
    messageId: 'old-reply', taskId: 'task-a', role: 'assistant',
    replyToMessageId: oldUser.messageId, runId: 'old-run', runStatus: 'completed', content: '旧结果',
  }] })
  const store = actualStore({
    createWorkTaskRunStream: async () => { throw new Error('agent unavailable') },
    getWorkTask: async () => reply(persisted),
  })
  store.currentTask = persisted
  await assert.rejects(
    store.runMessage({ agentId, message: '新消息', clientMessageId: 'new-key' }),
    /agent unavailable/,
  )
  assert.equal(store.runState, 'failed')
  assert.equal(store.retryAttempt.kind, 'uncertain')
  assert.equal(store.retryAttempt.clientMessageId, 'new-key')
  assert.equal(store.draftMessage, '新消息')
})

await check('another client claim cannot confirm a newly rejected request', async () => {
  let detailReads = 0
  const otherUser = {
    messageId: 'other-user', taskId: 'task-a', role: 'user',
    clientMessageId: 'other-key', content: '其他客户端消息',
  }
  const store = actualStore({
    createWorkTaskRunStream: async () => { throw new Error('task already running') },
    getWorkTask: async () => {
      detailReads += 1
      const pending = detailReads === 1
      return reply(task('task-a', {
        currentRunId: pending ? 'other-run' : null,
        messages: [otherUser, {
          messageId: 'other-reply', taskId: 'task-a', role: 'assistant',
          replyToMessageId: otherUser.messageId, runId: 'other-run',
          runStatus: pending ? 'pending' : 'completed', content: '其他客户端结果',
        }],
      }))
    },
  })
  store.currentTask = task('task-a')
  await assert.rejects(
    store.runMessage({ agentId, message: '本次请求', clientMessageId: 'this-key' }),
    /task already running/,
  )
  assert.equal(detailReads, 2)
  assert.equal(store.retryAttempt.clientMessageId, 'this-key')
  assert.equal(store.retryAttempt.kind, 'uncertain')
  assert.equal(store.draftMessage, '本次请求')
})

await check('session reset clears loading flags, errors and pagination while rejecting late reads', async () => {
  const listing = deferred()
  const detail = deferred()
  const store = actualStore({ listWorkTasks: () => listing.promise, getWorkTask: () => detail.promise })
  store.pageNum = 4
  store.pageSize = 50
  const pendingList = store.loadTasks({ keyword: 'private', pageNum: 4, pageSize: 50 })
  const pendingDetail = store.loadTask('old-task')
  store.listError = 'old list error'
  store.detailError = 'old detail error'
  clearSessionState(store)
  assert.equal(store.listLoading, false)
  assert.equal(store.detailLoading, false)
  assert.equal(store.listError, '')
  assert.equal(store.detailError, '')
  assert.equal(store.pageNum, 1)
  assert.equal(store.pageSize, 20)
  listing.resolve({ rows: [task('old-task')], total: 1, pageNum: 4, pageSize: 50 })
  detail.resolve(reply(task('old-task')))
  assert.deepEqual(await Promise.all([pendingList, pendingDetail]), [false, false])
  assert.equal(store.tasks.length, 0)
  assert.equal(store.currentTask, null)
})

if (failures.length) {
  throw new AggregateError(failures.map(({ error }) => error),
    failures.map(({ name }) => name).join('; '))
}
